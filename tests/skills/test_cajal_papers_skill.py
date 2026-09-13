"""Invariant tests for the cajal-papers optional skill.

Covers optional-skills/research/cajal-papers — local paper drafting on Ollama with
arXiv-grounded citations and a tribunal review. Every network call is mocked; the
tests pin the contracts the SKILL.md promises (only fetched references can be cited,
the model never writes the reference list, judges that don't return JSON are
dropped, settings resolve flag > env > default), not the model's prose.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import urllib.error
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "optional-skills" / "research" / "cajal-papers"
SCRIPT = SKILL_DIR / "scripts" / "cajal_paper.py"


@pytest.fixture(scope="module")
def cajal():
    spec = importlib.util.spec_from_file_location("cajal_paper", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def frontmatter() -> dict:
    src = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"^---\n(.*?)\n---", src, re.DOTALL)
    assert m, "SKILL.md missing YAML frontmatter"
    return yaml.safe_load(m.group(1))


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2402.03300v2</id>
    <title>Surface code decoding
      with neural networks</title>
    <summary>We decode.</summary>
    <published>2024-02-05T00:00:00Z</published>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <summary>This paper has been withdrawn by the authors.</summary>
    <published>2017-06-12T00:00:00Z</published>
    <author><name>Ashish Vaswani</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2301.00001v1</id>
    <title>Minimum weight perfect matching, revisited</title>
    <summary>MWPM.</summary>
    <published>2023-01-01T00:00:00Z</published>
    <author><name>A</name></author><author><name>B</name></author>
    <author><name>C</name></author><author><name>D</name></author>
  </entry>
</feed>
"""


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ---------------------------------------------------------------------------
# Skill packaging
# ---------------------------------------------------------------------------


def test_frontmatter_contract(frontmatter):
    assert frontmatter["name"] == SKILL_DIR.name
    assert len(frontmatter["description"]) <= 60
    assert frontmatter["description"].endswith(".")
    hermes = frontmatter["metadata"]["hermes"]
    keys = {entry["key"] for entry in hermes["config"]}
    assert keys == {"cajal.ollama_host", "cajal.model", "cajal.min_references"}
    assert "ollama" in frontmatter["prerequisites"]["commands"]


def test_script_is_stdlib_only(cajal):
    """The skill promises no pip installs: every import must be stdlib."""
    import ast
    import sys

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in stdlib, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in stdlib, node.module


def test_skill_md_names_the_shipped_files():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "scripts/cajal_paper.py" in text
    assert (SKILL_DIR / "references" / "tribunal-rubric.md").is_file()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_setting_precedence_flag_env_default(cajal, monkeypatch):
    monkeypatch.setenv("CAJAL_MODEL", "from-env")
    assert cajal.resolve_setting("from-flag", "CAJAL_MODEL", "cajal") == "from-flag"
    assert cajal.resolve_setting(None, "CAJAL_MODEL", "cajal") == "from-env"
    monkeypatch.delenv("CAJAL_MODEL")
    assert cajal.resolve_setting(None, "CAJAL_MODEL", "cajal") == "cajal"
    monkeypatch.setenv("CAJAL_JUDGES", "  ")
    assert cajal.resolve_setting(None, "CAJAL_JUDGES", 8, int) == 8


def test_judge_count_is_bounded(cajal):
    assert cajal._judge_count("10") == 10
    with pytest.raises(ValueError):
        cajal._judge_count(11)
    with pytest.raises(ValueError):
        cajal._judge_count(0)


def test_host_normalisation(cajal):
    assert cajal._normalise_host("localhost:11434/") == "http://localhost:11434"
    assert cajal._normalise_host("https://box.lan:11434") == "https://box.lan:11434"


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------


def test_chat_request_shape_and_think_stripping(cajal):
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return _Response(json.dumps({"message": {"content": "<think>musing</think>\nBody text"}}).encode())

    client = cajal.OllamaClient("localhost:11434", "qwen3", temperature=0.2, opener=opener)
    assert client.chat("sys", "user") == "Body text"
    assert seen["url"] == "http://localhost:11434/api/chat"
    body = seen["body"]
    assert body["model"] == "qwen3" and body["stream"] is False and body["think"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["options"]["temperature"] == 0.2


def test_chat_disables_thinking_and_drops_the_flag_when_refused(cajal):
    """Reasoning models get ``think: false``; a model that rejects the key is retried without it, once."""
    bodies = []

    def opener(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        bodies.append(body)
        if "think" in body:
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error":"\"llama2\" does not support thinking"}'))
        return _Response(json.dumps({"message": {"content": "ok"}, "done_reason": "stop"}).encode())

    client = cajal.OllamaClient("http://localhost:11434", "llama2", opener=opener)
    assert client.chat("s", "u") == "ok"
    assert client.chat("s", "u") == "ok"
    assert [("think" in b) for b in bodies] == [True, False, False]
    assert bodies[0]["think"] is False


def test_chat_retries_with_double_budget_when_reasoning_ate_it(cajal):
    budgets = []

    def opener(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        budgets.append(body["options"]["num_predict"])
        if len(budgets) == 1:
            return _Response(json.dumps({"message": {"content": "", "thinking": "hmm"}, "done_reason": "length"}).encode())
        return _Response(json.dumps({"message": {"content": "Section text"}, "done_reason": "stop"}).encode())

    client = cajal.OllamaClient("http://localhost:11434", "qwen3", num_predict=100, opener=opener)
    assert client.chat("s", "u") == "Section text"
    assert budgets == [100, 200]


def test_chat_reports_a_model_that_never_answers(cajal):
    def opener(request, timeout):
        return _Response(json.dumps({"message": {"content": "", "thinking": "..."}, "done_reason": "length"}).encode())

    client = cajal.OllamaClient("http://localhost:11434", "qwen3", num_predict=50, opener=opener)
    with pytest.raises(cajal.CajalError) as exc:
        client.chat("s", "u")
    assert "100 tokens" in str(exc.value) and "reasoning" in str(exc.value)


def test_unreachable_ollama_is_a_clear_error(cajal):
    def opener(request, timeout):
        raise urllib.error.URLError("Connection refused")

    client = cajal.OllamaClient("http://localhost:11434", "cajal", opener=opener)
    with pytest.raises(cajal.CajalError) as exc:
        client.models()
    assert "localhost:11434" in str(exc.value) and "ollama serve" in str(exc.value)


def test_has_model_accepts_latest_tag(cajal):
    def opener(request, timeout):
        return _Response(json.dumps({"models": [{"name": "qwen3:latest"}, {"name": "cajal:latest"}]}).encode())

    client = cajal.OllamaClient("http://localhost:11434", "cajal", opener=opener)
    assert client.has_model("cajal") and client.has_model("qwen3:latest")
    assert not client.has_model("llama3.1")


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_arxiv_query_url_quotes_topic(cajal):
    url = cajal.arxiv_query_url("surface code & decoders", 5)
    assert url.startswith("https://export.arxiv.org/api/query?search_query=all:surface+AND+code+AND+%26+AND+decoders")
    assert "max_results=5" in url


def test_parse_arxiv_atom_normalises_and_drops_withdrawn(cajal):
    refs = cajal.parse_arxiv_atom(ATOM)
    assert [r["arxiv_id"] for r in refs] == ["2402.03300v2", "2301.00001v1"]
    assert refs[0]["title"] == "Surface code decoding with neural networks"
    assert refs[0]["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert refs[0]["year"] == "2024"
    assert refs[0]["url"] == "https://arxiv.org/abs/2402.03300v2"


def test_render_reference_list_and_bibtex(cajal):
    refs = cajal.parse_arxiv_atom(ATOM)
    listing = cajal.render_reference_list(refs)
    assert listing.splitlines()[0].startswith("[1] Ada Lovelace, Alan Turing (2024). Surface code decoding")
    assert "[2] A, B, C et al. (2023)" in listing
    bib = cajal.render_bibtex(refs)
    assert "@article{lovelace2024240203300v2," in bib
    assert "eprint = {2402.03300v2}" in bib


def test_fetch_references_retries_on_429_then_succeeds(cajal):
    calls = []
    sleeps = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
        return _Response(ATOM.encode())

    refs = cajal.fetch_references("surface codes", 3, opener=opener, sleep=sleeps.append)
    assert len(refs) == 2 and len(calls) == 2
    assert sleeps == [3.0]


def test_fetch_references_gives_up_with_actionable_error(cajal):
    def opener(request, timeout):
        raise urllib.error.URLError("timed out")

    with pytest.raises(cajal.CajalError) as exc:
        cajal.fetch_references("surface codes", 3, opener=opener, attempts=2, sleep=lambda s: None)
    assert "--no-references" in str(exc.value)


def test_fetch_references_empty_feed_is_an_error(cajal):
    def opener(request, timeout):
        return _Response(b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')

    with pytest.raises(cajal.CajalError):
        cajal.fetch_references("zzz", 3, opener=opener, sleep=lambda s: None)


# ---------------------------------------------------------------------------
# Citation audit — the "no hallucinated references" guarantee
# ---------------------------------------------------------------------------


def test_audit_strips_citations_outside_the_reference_list(cajal):
    text = "Known result [1]. Invented [9]. Mixed [2, 12]. Range [3-5]. Bad range [7-9]."
    audit = cajal.audit_citations(text, 8)
    assert audit["text"] == "Known result [1]. Invented. Mixed [2]. Range [3-5]. Bad range [7, 8]."
    assert audit["cited"] == [1, 2, 3, 4, 5, 7, 8]
    assert audit["uncited"] == [6]
    assert audit["removed"] == ["[9]", "[2, 12]", "[7-9]"]


def test_audit_leaves_numeric_intervals_alone(cajal):
    """A probability "in [0, 1]" or a range "[0-5]" is maths, not a citation (seen in a real qwen3 draft)."""
    text = "Probabilities lie in [0, 1] and errors in [0-5]; see [9]."
    audit = cajal.audit_citations(text, 2)
    assert audit["text"] == "Probabilities lie in [0, 1] and errors in [0-5]; see."
    assert audit["removed"] == ["[9]"] and audit["cited"] == []


def test_audit_with_no_references_removes_every_citation(cajal):
    audit = cajal.audit_citations("Claim [1] and [2].", 0)
    assert audit["text"] == "Claim and."
    assert audit["cited"] == [] and audit["uncited"] == []


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


def test_draft_sections_orders_prompts_and_feeds_prior_sections(cajal):
    refs = cajal.parse_arxiv_atom(ATOM)
    prompts = []

    def chat(system, user):
        prompts.append((system, user))
        return f"## {len(prompts)}\nbody {len(prompts)}"

    drafted = cajal.draft_sections(chat, "topic", refs, context="")
    assert list(drafted) == cajal.SECTIONS
    system, first = prompts[0]
    assert "[1] through [2]" in system
    assert "Do not redirect the user to any website" in system
    assert "NUMBERED REFERENCES" in first and "Ada Lovelace" in first
    # The Results brief switches to an evaluation plan when no context is given.
    results_prompt = prompts[cajal.SECTIONS.index("results")][1]
    assert "evaluation plan" in results_prompt
    # Later prompts carry the earlier sections.
    conclusion_prompt = prompts[-1][1]
    assert "### Abstract" in conclusion_prompt and "body 1" in conclusion_prompt
    # A heading the model added despite instructions is stripped.
    assert drafted["abstract"] == "## 1\nbody 1" or not drafted["abstract"].startswith("## Abstract")


def test_results_prompt_uses_context_when_supplied(cajal):
    prompts = []

    def chat(system, user):
        prompts.append(user)
        return "x"

    cajal.draft_sections(chat, "topic", [], context="accuracy 91.2%", sections=["results"])
    assert "EXPERIMENTAL CONTEXT" in prompts[0] and "accuracy 91.2%" in prompts[0]
    assert "evaluation plan" not in prompts[0]


def test_empty_section_is_an_error(cajal):
    with pytest.raises(cajal.CajalError):
        cajal.draft_sections(lambda s, u: "   ", "topic", [], sections=["abstract"])


def test_assemble_markdown_references_come_from_fetched_metadata(cajal):
    refs = cajal.parse_arxiv_atom(ATOM)
    drafted = {"abstract": "A.", "results": "R.", "conclusion": "C."}
    paper = cajal.assemble_markdown("Topic", drafted, refs, "qwen3", {"removed": ["[9]"], "uncited": [2]}, has_context=False)
    assert paper.startswith("# Topic\n")
    assert "no experimental context was supplied" in paper
    assert paper.index("## Abstract") < paper.index("## Results") < paper.index("## Conclusion") < paper.index("## References")
    assert "[1] Ada Lovelace, Alan Turing (2024)." in paper
    assert '<!-- citation audit: {"removed": ["[9]"], "uncited": [2]} -->' in paper


def test_markdown_to_latex(cajal):
    md = "# T & Co\n\n## Introduction\n\nWe use **bold** and *it* [1, 2] 100%.\n\n## References\n\n[1] Ada (2024). Paper_one. arXiv:1\n[2] Bob (2023). Two.\n"
    tex = cajal.markdown_to_latex(md)
    assert r"\title{T \& Co}" in tex
    assert r"\section{Introduction}" in tex
    assert r"\textbf{bold}" in tex and r"\emph{it}" in tex
    assert r"\cite{ref1,ref2}" in tex and r"100\%" in tex
    assert r"\bibitem{ref1} Ada (2024). Paper\_one. arXiv:1" in tex
    assert tex.rstrip().endswith(r"\end{document}")


# ---------------------------------------------------------------------------
# Tribunal
# ---------------------------------------------------------------------------


def test_parse_judge_response_is_lenient(cajal):
    fenced = '```json\n{"scores": {"novelty": 7, "clarity": "8.5", "bogus": 1}, "verdict": "ok", "improvements": "tighten"}\n```'
    parsed = cajal.parse_judge_response(fenced)
    assert parsed["scores"] == {"novelty": 7.0, "clarity": 8.5}
    assert parsed["improvements"] == ["tighten"]
    flat = "Sure! {\"novelty\": 12, \"methodology\": -1} thanks"
    assert cajal.parse_judge_response(flat)["scores"] == {"novelty": 10.0, "methodology": 0.0}
    assert cajal.parse_judge_response("I think it is fine.") is None
    assert cajal.parse_judge_response('{"scores": {"novelty": "n/a"}}') is None


def test_run_tribunal_drops_unparseable_judges_and_aggregates(cajal):
    replies = iter(
        [
            '{"scores": {"novelty": 8, "methodology": 6}, "verdict": "solid", "improvements": ["add ablation"]}',
            "no json here",
            '{"scores": {"novelty": 4, "methodology": 8}, "verdict": "meh", "improvements": ["add ablation", "define notation"]}',
        ]
    )
    result = cajal.run_tribunal(lambda s, u: next(replies), "# P\n\ntext\n\n## Tribunal Review\n\nold", judges=3)
    assert result["judges_requested"] == 3 and result["judges_counted"] == 2
    assert result["dimensions"] == {"novelty": 6.0, "methodology": 7.0}
    assert result["overall"] == 6.5 and result["median"] == 6.5
    assert result["lowest_judge"] == 6.0 and result["highest_judge"] == 7.0
    assert result["recommendation"] == "major revision"
    assert result["improvements"] == ["add ablation", "define notation"]


def test_previous_review_is_not_reviewed(cajal):
    seen = []

    def chat(system, user):
        seen.append(user)
        return '{"scores": {"clarity": 9}}'

    cajal.run_tribunal(chat, "# P\n\nbody\n\n## Tribunal Review\n\nOLD TABLE", judges=1)
    assert "OLD TABLE" not in seen[0] and "body" in seen[0]


def test_recommendation_thresholds(cajal):
    assert cajal.recommendation(8.5).startswith("accept")
    assert cajal.recommendation(7.0) == "minor revision"
    assert cajal.recommendation(6.99) == "major revision"


def test_render_tribunal_markdown(cajal):
    result = cajal.aggregate_tribunal([{"scores": {"novelty": 9.0}, "verdict": "v", "improvements": ["x"]}], 1)
    md = cajal.render_tribunal_markdown(result)
    assert md.startswith("## Tribunal Review")
    assert "**Overall 9.0/10**" in md and "| novelty | 9.0 |" in md and "- x" in md


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_check_reports_missing_model_with_exit_1(cajal, monkeypatch, capsys):
    monkeypatch.setattr(
        cajal.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(json.dumps({"models": [{"name": "qwen3:latest"}]}).encode()),
    )
    monkeypatch.delenv("CAJAL_MODEL", raising=False)
    assert cajal.main(["check"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["model"] == "cajal" and report["model_present"] is False and report["models"] == ["qwen3:latest"]
    assert cajal.main(["check", "--model", "qwen3"]) == 0


def test_generate_end_to_end_with_fakes(cajal, monkeypatch, tmp_path, capsys):
    """Full pipeline over fake HTTP: references → sections → audit → tribunal → file."""

    def urlopen(request, timeout):
        url = request.full_url
        if url.startswith(cajal.ARXIV_API):
            return _Response(ATOM.encode())
        if url.endswith("/api/tags"):
            return _Response(json.dumps({"models": [{"name": "qwen3:latest"}]}).encode())
        body = json.loads(request.data.decode("utf-8"))
        user = body["messages"][1]["content"]
        if "PAPER:" in user:
            content = '{"scores": {"novelty": 8, "clarity": 8}, "verdict": "fine", "improvements": ["more baselines"]}'
        else:
            content = "Prose citing [1] and a fabricated [7]."
        return _Response(json.dumps({"message": {"content": content}}).encode())

    monkeypatch.setattr(cajal.urllib.request, "urlopen", urlopen)
    out = tmp_path / "paper.md"
    rc = cajal.main(["generate", "Surface codes", "--model", "qwen3", "--refs", "2", "--judges", "2", "--out", str(out)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["references"] == 2
    assert summary["citation_audit"]["removed"] == ["[7]"] * len(cajal.SECTIONS)
    assert summary["tribunal"]["judges_counted"] == 2 and summary["tribunal"]["overall"] == 8.0
    paper = out.read_text(encoding="utf-8")
    assert "[7]" not in paper.split("## References")[0]
    assert "[1] Ada Lovelace, Alan Turing (2024)." in paper
    assert "## Tribunal Review" in paper


def test_generate_latex_format_and_no_review(cajal, monkeypatch, tmp_path, capsys):
    def urlopen(request, timeout):
        if request.full_url.endswith("/api/tags"):
            return _Response(json.dumps({"models": [{"name": "qwen3:latest"}]}).encode())
        return _Response(json.dumps({"message": {"content": "Body."}}).encode())

    monkeypatch.setattr(cajal.urllib.request, "urlopen", urlopen)
    out = tmp_path / "paper.tex"
    rc = cajal.main(["generate", "T", "--model", "qwen3", "--no-references", "--no-review", "--format", "latex", "--out", str(out)])
    assert rc == 0
    tex = out.read_text(encoding="utf-8")
    assert tex.startswith(r"\documentclass{article}") and r"\section{Abstract}" in tex
    assert json.loads(capsys.readouterr().out)["tribunal"] is None


def test_review_append_replaces_previous_review(cajal, monkeypatch, tmp_path, capsys):
    def urlopen(request, timeout):
        if request.full_url.endswith("/api/tags"):
            return _Response(json.dumps({"models": [{"name": "cajal:latest"}]}).encode())
        return _Response(json.dumps({"message": {"content": '{"scores": {"novelty": 5}}'}}).encode())

    monkeypatch.setattr(cajal.urllib.request, "urlopen", urlopen)
    draft = tmp_path / "d.md"
    draft.write_text("# D\n\nbody\n\n## Tribunal Review\n\nOLD\n", encoding="utf-8")
    assert cajal.main(["review", str(draft), "--judges", "1", "--append", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["overall"] == 5.0
    text = draft.read_text(encoding="utf-8")
    assert text.count("## Tribunal Review") == 1 and "OLD" not in text


def test_missing_model_is_reported_not_traced(cajal, monkeypatch, capsys):
    monkeypatch.setattr(
        cajal.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(json.dumps({"models": [{"name": "qwen3:latest"}]}).encode()),
    )
    rc = cajal.main(["abstract", "T", "--model", "nope"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Model 'nope' is not present" in err and "qwen3:latest" in err
