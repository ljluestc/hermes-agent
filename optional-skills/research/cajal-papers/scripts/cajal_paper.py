#!/usr/bin/env python3
"""Local scientific paper drafting against an Ollama model, CAJAL-style.

Stdlib only. Everything talks to two HTTP endpoints:

  * Ollama's ``/api/chat`` on the configured host — section-by-section drafting
    and the tribunal review. Nothing leaves the machine unless the host does.
  * arXiv's export API — the *only* source of references. The model is handed
    a numbered list of real papers and may cite ``[1]..[N]``; every other
    bracket citation is stripped by the citation audit. The References section
    is rendered from the fetched metadata, never written by the model.

Subcommands
-----------
  check                         Ollama reachable? configured model present?
  references "topic"            fetch N real arXiv references (markdown / bibtex / json)
  abstract   "topic"            draft one section
  methods    "topic"            draft one section
  generate   "topic"            full 7-section draft + citation audit + tribunal review
  review     draft.md           tribunal review of an existing markdown draft

Settings resolve flag > environment > default:

  --host   / OLLAMA_HOST        default http://localhost:11434
  --model  / CAJAL_MODEL        default "cajal"
  --refs   / CAJAL_MIN_REFERENCES  default 8
  --judges / CAJAL_JUDGES       default 8 (1..10)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "cajal"
DEFAULT_MIN_REFERENCES = 8
DEFAULT_JUDGES = 8
MAX_JUDGES = 10
ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# Section order of a CAJAL paper. ``results`` is the only section whose
# prompt changes depending on whether the user supplied experimental context.
SECTIONS: List[str] = [
    "abstract",
    "introduction",
    "related_work",
    "methods",
    "results",
    "discussion",
    "conclusion",
]
SECTION_TITLES: Dict[str, str] = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "related_work": "Related Work",
    "methods": "Methodology",
    "results": "Results",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
}
SECTION_BRIEFS: Dict[str, str] = {
    "abstract": (
        "Write the Abstract: 150-250 words in one paragraph covering background, "
        "the method, the key results, and the conclusion. No citations, no headings."
    ),
    "introduction": (
        "Write the Introduction: context, a precise problem statement, the objectives, "
        "and why the problem matters. End with a short paragraph listing the contributions."
    ),
    "related_work": (
        "Write the Related Work section as a critical synthesis of the numbered references. "
        "Cite at least five of them, group them by theme, and state the gap this paper addresses."
    ),
    "methods": (
        "Write the Methodology: a reproducible description of the approach, its assumptions, "
        "the experimental design, and the evaluation protocol. Use precise notation where it helps."
    ),
    "results": (
        "Write the Results section strictly from the EXPERIMENTAL CONTEXT block. Report only "
        "numbers that appear there; do not invent measurements."
    ),
    "results_no_data": (
        "Write the Results section as an evaluation plan. No experiments were supplied, so do NOT "
        "report measured numbers. Describe the metrics, baselines, datasets, and the outcomes the "
        "methodology predicts, and label predictions explicitly as expected rather than observed."
    ),
    "discussion": (
        "Write the Discussion: interpretation of the results, threats to validity, limitations, "
        "and future work. Be candid about what is not yet shown."
    ),
    "conclusion": (
        "Write the Conclusion: a compact summary of the contributions and their implications, "
        "one paragraph, no new claims."
    ),
}

# Ten reviewers, each with a different bias. ``--judges K`` takes the first K.
JUDGE_PERSONAS: List[str] = [
    "a methodologist who checks whether the design can actually answer the research question",
    "a statistician who checks whether every reported number is justified by the described protocol",
    "a domain expert who checks the technical claims against the state of the art",
    "a sceptical reviewer who looks for overclaiming and unsupported generalisation",
    "a reproducibility auditor who checks whether another lab could rerun the work from the text",
    "a citation checker who checks that each citation supports the sentence it is attached to",
    "a clarity editor who checks structure, precision of language, and readability",
    "a senior area chair who weighs novelty and significance for a top venue",
    "an ethics and impact reviewer who looks for missing limitations and societal risks",
    "an early-career researcher who checks whether the paper teaches something clearly",
]
DIMENSIONS: List[str] = [
    "novelty",
    "methodology",
    "citation_quality",
    "argument_strength",
    "reproducibility",
    "clarity",
    "technical_depth",
    "publishability",
]

# Section titles the reviewer strips from a draft that already carries one.
_REVIEW_HEADING = re.compile(r"^##\s+Tribunal Review\b.*$", re.M)
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S)
_CITATION = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
_JSON_OBJECT = re.compile(r"\{.*\}", re.S)
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class CajalError(RuntimeError):
    """Any failure the agent should report verbatim to the user."""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def resolve_setting(flag: Any, env_name: str, default: Any, cast: Callable[[Any], Any] = str) -> Any:
    """Flag wins, then the environment variable, then the default."""
    if flag is not None and flag != "":
        return cast(flag)
    env_value = os.environ.get(env_name, "")
    if env_value.strip():
        return cast(env_value.strip())
    return cast(default)


def _positive_int(value: Any) -> int:
    number = int(value)
    if number < 1:
        raise ValueError(f"expected a positive integer, got {value!r}")
    return number


def _judge_count(value: Any) -> int:
    number = _positive_int(value)
    if number > MAX_JUDGES:
        raise ValueError(f"at most {MAX_JUDGES} judges, got {number}")
    return number


def _normalise_host(host: str) -> str:
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------


class OllamaClient:
    """Minimal ``/api/chat`` and ``/api/tags`` client. ``opener`` is injectable for tests."""

    def __init__(
        self,
        host: str,
        model: str,
        temperature: float = 0.3,
        num_predict: int = 3072,
        timeout: float = 600.0,
        opener: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.host = _normalise_host(host)
        self.model = model
        self.temperature = temperature
        self.num_predict = num_predict
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen
        # Reasoning models (qwen3, deepseek-r1) otherwise spend the whole
        # ``num_predict`` budget in the ``thinking`` field and return an empty
        # section. Ollama rejects ``think`` for models without the capability,
        # so the flag is dropped after the first such refusal.
        self._send_think = True

    def _request(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self.host + path
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with self._opener(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # pragma: no cover - best effort
                pass
            raise CajalError(f"Ollama at {self.host} returned HTTP {exc.code} for {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CajalError(
                f"Cannot reach Ollama at {self.host} ({exc.reason}). Start it with `ollama serve` "
                f"or pass --host / set OLLAMA_HOST."
            ) from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise CajalError(f"Ollama at {self.host} returned non-JSON for {path}: {body[:200]!r}") from exc

    def models(self) -> List[str]:
        payload = self._request("/api/tags")
        return sorted(str(m.get("name", "")) for m in payload.get("models", []) if m.get("name"))

    def has_model(self, name: str) -> bool:
        names = set(self.models())
        return name in names or f"{name}:latest" in names

    def _chat_once(self, system: str, user: str, temperature: float, num_predict: int) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        if self._send_think:
            payload["think"] = False
        try:
            return self._request("/api/chat", payload)
        except CajalError as exc:
            if self._send_think and "HTTP 400" in str(exc) and "think" in str(exc).lower():
                self._send_think = False
                del payload["think"]
                return self._request("/api/chat", payload)
            raise

    def chat(self, system: str, user: str, temperature: Optional[float] = None, num_predict: Optional[int] = None) -> str:
        temperature = self.temperature if temperature is None else temperature
        budget = self.num_predict if num_predict is None else num_predict
        response = self._chat_once(system, user, temperature, budget)
        message = response.get("message") or {}
        content = strip_thinking(str(message.get("content") or "")).strip()
        if not content and response.get("error"):
            raise CajalError(f"Ollama error: {response['error']}")
        if not content and response.get("done_reason") == "length":
            # The budget went to reasoning; one retry with twice the room.
            response = self._chat_once(system, user, temperature, budget * 2)
            message = response.get("message") or {}
            content = strip_thinking(str(message.get("content") or "")).strip()
            if not content:
                raise CajalError(
                    f"{self.model} produced no answer within {budget * 2} tokens (all of it went to reasoning). "
                    "Use a non-reasoning model, or a smaller topic."
                )
        return content


def strip_thinking(text: str) -> str:
    """Drop ``<think>...</think>`` reasoning that some models inline in the answer."""
    return _THINK_BLOCK.sub("", text)


# ---------------------------------------------------------------------------
# arXiv references
# ---------------------------------------------------------------------------


def arxiv_query_url(topic: str, count: int) -> str:
    terms = "+AND+".join(urllib.parse.quote(t) for t in topic.split())
    return (
        f"{ARXIV_API}?search_query=all:{terms}&max_results={count}"
        f"&sortBy=relevance&sortOrder=descending"
    )


def parse_arxiv_atom(xml_text: str) -> List[Dict[str, Any]]:
    """Atom feed -> list of reference dicts, in feed order."""
    root = ET.fromstring(xml_text)
    refs: List[Dict[str, Any]] = []
    for entry in root.findall("a:entry", ATOM_NS):
        raw_id = (entry.findtext("a:id", default="", namespaces=ATOM_NS) or "").strip()
        arxiv_id = raw_id.split("/abs/")[-1] if "/abs/" in raw_id else raw_id
        title = " ".join((entry.findtext("a:title", default="", namespaces=ATOM_NS) or "").split())
        summary = " ".join((entry.findtext("a:summary", default="", namespaces=ATOM_NS) or "").split())
        if summary.lower().startswith(("this paper has been withdrawn", "withdrawn")):
            continue
        authors = [
            " ".join((author.findtext("a:name", default="", namespaces=ATOM_NS) or "").split())
            for author in entry.findall("a:author", ATOM_NS)
        ]
        published = (entry.findtext("a:published", default="", namespaces=ATOM_NS) or "")[:10]
        if not arxiv_id or not title:
            continue
        refs.append(
            {
                "arxiv_id": arxiv_id,
                "title": title,
                "authors": [a for a in authors if a],
                "year": published[:4],
                "published": published,
                "summary": summary[:600],
                "url": f"https://arxiv.org/abs/{arxiv_id}",
            }
        )
    return refs


def fetch_references(
    topic: str,
    count: int,
    opener: Optional[Callable[..., Any]] = None,
    timeout: float = 60.0,
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> List[Dict[str, Any]]:
    """One arXiv query, retried with a 3 s / 6 s / 9 s back-off on 429, 5xx, or a timeout.

    arXiv asks clients to leave ~3 s between requests, so a burst from a previous
    call (or another tool) surfaces as HTTP 429; waiting it out is the fix. 503 is
    what the export API returns while it is overloaded, which is equally transient.
    """
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(arxiv_query_url(topic, count), headers={"User-Agent": "hermes-cajal-papers/1.0"})
    last_error: Optional[BaseException] = None
    xml_text = ""
    for attempt in range(1, attempts + 1):
        try:
            with opener(request, timeout=timeout) as response:
                xml_text = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if (exc.code != 429 and exc.code < 500) or attempt == attempts:
                raise CajalError(f"arXiv lookup failed (HTTP {exc.code}); retry, or pass --no-references to draft without citations.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                reason = getattr(exc, "reason", exc)
                raise CajalError(f"arXiv lookup failed ({reason}); retry, or pass --no-references to draft without citations.") from exc
        sleep(3.0 * attempt)
    if not xml_text and last_error is not None:  # pragma: no cover - defensive
        raise CajalError(f"arXiv lookup failed ({last_error})")
    try:
        refs = parse_arxiv_atom(xml_text)
    except ET.ParseError as exc:
        raise CajalError(f"arXiv returned unparseable XML: {exc}") from exc
    if not refs:
        raise CajalError(f"arXiv returned no papers for {topic!r}; broaden the topic or pass --no-references.")
    return refs


def _author_list(authors: Sequence[str], limit: int = 3) -> str:
    if not authors:
        return "Unknown"
    if len(authors) > limit:
        return ", ".join(authors[:limit]) + " et al."
    return ", ".join(authors)


def render_reference_list(refs: Sequence[Dict[str, Any]]) -> str:
    lines = []
    for n, ref in enumerate(refs, start=1):
        lines.append(f"[{n}] {_author_list(ref['authors'])} ({ref['year'] or 'n.d.'}). {ref['title']}. arXiv:{ref['arxiv_id']}. {ref['url']}")
    return "\n".join(lines)


def render_bibtex(refs: Sequence[Dict[str, Any]]) -> str:
    entries = []
    for n, ref in enumerate(refs, start=1):
        last = (ref["authors"][0].split()[-1].lower() if ref["authors"] else "anon")
        key = _SLUG_STRIP.sub("", f"{last}{ref['year']}{ref['arxiv_id']}")
        entries.append(
            "\n".join(
                [
                    f"@article{{{key},",
                    f"  title = {{{ref['title']}}},",
                    f"  author = {{{' and '.join(ref['authors']) or 'Unknown'}}},",
                    f"  year = {{{ref['year']}}},",
                    f"  eprint = {{{ref['arxiv_id']}}},",
                    "  archivePrefix = {arXiv},",
                    f"  url = {{{ref['url']}}},",
                    f"  note = {{[{n}]}}",
                    "}",
                ]
            )
        )
    return "\n\n".join(entries)


# ---------------------------------------------------------------------------
# Citation audit
# ---------------------------------------------------------------------------


def _expand_citation(group: str) -> List[int]:
    numbers: List[int] = []
    for part in re.split(r"\s*,\s*", group):
        if re.search(r"[-–]", part):
            lo, hi = re.split(r"\s*[-–]\s*", part, maxsplit=1)
            lo_i, hi_i = int(lo), int(hi)
            if hi_i < lo_i or hi_i - lo_i > 50:
                numbers.append(lo_i)
                numbers.append(hi_i)
            else:
                numbers.extend(range(lo_i, hi_i + 1))
        else:
            numbers.append(int(part))
    return numbers


def audit_citations(text: str, reference_count: int) -> Dict[str, Any]:
    """Strip every ``[n]`` with n outside 1..reference_count; report what was cited."""
    cited: set = set()
    removed: List[str] = []

    def _replace(match: "re.Match[str]") -> str:
        numbers = _expand_citation(match.group(1))
        if 0 in numbers:
            return match.group(0)  # "[0, 1]" is an interval, never a citation
        valid = [n for n in numbers if 1 <= n <= reference_count]
        invalid = [n for n in numbers if n not in valid]
        cited.update(valid)
        if invalid:
            removed.append(match.group(0))
        if not valid:
            return ""
        if len(valid) == len(numbers):
            return match.group(0)
        return "[" + ", ".join(str(n) for n in sorted(set(valid))) + "]"

    cleaned = _CITATION.sub(_replace, text)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned)  # "claim [9]." -> "claim."
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    uncited = [n for n in range(1, reference_count + 1) if n not in cited]
    return {
        "text": cleaned,
        "cited": sorted(cited),
        "uncited": uncited,
        "removed": removed,
    }


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


def system_prompt(reference_count: int, has_context: bool) -> str:
    rules = [
        "You are CAJAL, a scientific writing assistant drafting one section of a research paper at a time.",
        "Write formal academic English in Markdown. Output only the requested section body: no title line, no heading, no preamble, no closing remarks.",
        "Never invent authors, papers, datasets, or measurements.",
    ]
    if reference_count:
        rules.append(
            f"Cite only the numbered references provided, as [1] through [{reference_count}], "
            "using the bracket number inline. Do not cite anything else and do not write a reference list."
        )
    else:
        rules.append("No references are available: do not cite anything and do not write a reference list.")
    if has_context:
        rules.append("Numbers and findings must come from the EXPERIMENTAL CONTEXT block; if it lacks something, say so instead of guessing.")
    rules.append("Do not redirect the user to any website or external service; the draft is written here.")
    return "\n".join(f"- {rule}" for rule in rules)


def section_prompt(
    section: str,
    topic: str,
    refs: Sequence[Dict[str, Any]],
    context: str,
    drafted: Dict[str, str],
    prior_budget: int = 6000,
) -> str:
    brief_key = "results_no_data" if section == "results" and not context.strip() else section
    parts = [f"PAPER TOPIC: {topic}", ""]
    if refs:
        parts += ["NUMBERED REFERENCES (the only citable sources):", render_reference_list(refs), ""]
    if context.strip():
        parts += ["EXPERIMENTAL CONTEXT (supplied by the author):", context.strip(), ""]
    prior = []
    for done in SECTIONS:
        if done in drafted and done != section:
            prior.append(f"### {SECTION_TITLES[done]}\n{drafted[done]}")
    if prior:
        joined = "\n\n".join(prior)
        if len(joined) > prior_budget:
            joined = joined[-prior_budget:]
            joined = "[...earlier text trimmed...]\n" + joined
        parts += ["SECTIONS DRAFTED SO FAR (keep consistent with them):", joined, ""]
    parts += ["TASK:", SECTION_BRIEFS[brief_key]]
    return "\n".join(parts)


def draft_sections(
    chat: Callable[[str, str], str],
    topic: str,
    refs: Sequence[Dict[str, Any]],
    context: str = "",
    sections: Sequence[str] = SECTIONS,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, str]:
    """Draft ``sections`` in order; each prompt sees the sections before it."""
    system = system_prompt(len(refs), bool(context.strip()))
    drafted: Dict[str, str] = {}
    for section in sections:
        if section not in SECTION_TITLES:
            raise CajalError(f"unknown section {section!r}")
        if progress:
            progress(f"drafting {SECTION_TITLES[section]}")
        body = chat(system, section_prompt(section, topic, refs, context, drafted)).strip()
        body = re.sub(rf"^#+\s*{re.escape(SECTION_TITLES[section])}\s*\n", "", body, flags=re.I)
        if not body:
            raise CajalError(f"the model returned an empty {SECTION_TITLES[section]} section")
        drafted[section] = body
    return drafted


def slugify(topic: str) -> str:
    return _SLUG_STRIP.sub("-", topic.lower()).strip("-")[:60] or "paper"


def assemble_markdown(
    topic: str,
    drafted: Dict[str, str],
    refs: Sequence[Dict[str, Any]],
    model: str,
    audit: Optional[Dict[str, Any]] = None,
    has_context: bool = False,
) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# {topic}", "", f"*Draft generated locally with `{model}` via the cajal-papers skill on {stamp}.*", ""]
    if not has_context and "results" in drafted:
        lines += ["> **Note:** no experimental context was supplied, so the Results section is an evaluation plan, not measured data.", ""]
    for section in SECTIONS:
        if section in drafted:
            lines += [f"## {SECTION_TITLES[section]}", "", drafted[section], ""]
    if refs:
        lines += ["## References", "", render_reference_list(refs), ""]
    if audit and (audit.get("removed") or audit.get("uncited")):
        lines += ["<!-- citation audit: " + json.dumps({"removed": audit.get("removed", []), "uncited": audit.get("uncited", [])}) + " -->", ""]
    return "\n".join(lines).rstrip() + "\n"


def markdown_to_latex(markdown: str) -> str:
    """Small, deterministic conversion: headings, emphasis, bracket citations, references."""

    def esc(text: str) -> str:
        return (
            text.replace("\\", r"\textbackslash{}")
            .replace("&", r"\&")
            .replace("%", r"\%")
            .replace("$", r"\$")
            .replace("#", r"\#")
            .replace("_", r"\_")
            .replace("{", r"\{")
            .replace("}", r"\}")
        )

    title = ""
    body: List[str] = []
    refs: List[str] = []
    in_refs = False
    for line in markdown.splitlines():
        if line.startswith("<!--"):
            continue
        if line.startswith("# ") and not title:
            title = esc(line[2:].strip())
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            in_refs = heading.lower() == "references"
            if not in_refs:
                body.append(rf"\section{{{esc(heading)}}}")
            continue
        if line.startswith("### "):
            body.append(rf"\subsection{{{esc(line[4:].strip())}}}")
            continue
        if in_refs:
            m = re.match(r"\[(\d+)\]\s*(.*)", line.strip())
            if m:
                refs.append(rf"\bibitem{{ref{m.group(1)}}} {esc(m.group(2))}")
            continue
        if line.startswith("> "):
            body.append(rf"\emph{{{esc(line[2:].strip().replace('**', ''))}}}")
            continue
        text = esc(line)
        text = re.sub(r"\\_\\_(.+?)\\_\\_|\*\*(.+?)\*\*", lambda m: rf"\textbf{{{m.group(1) or m.group(2)}}}", text)
        text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\\emph{\1}", text)
        text = re.sub(r"\[(\d+(?:\s*,\s*\d+)*)\]", lambda m: r"\cite{" + ",".join("ref" + n.strip() for n in m.group(1).split(",")) + "}", text)
        body.append(text)
    out = [
        r"\documentclass{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{amsmath}",
        rf"\title{{{title or 'Untitled'}}}",
        r"\begin{document}",
        r"\maketitle",
        "",
        *body,
    ]
    if refs:
        out += ["", rf"\begin{{thebibliography}}{{{len(refs)}}}", *refs, r"\end{thebibliography}"]
    out += [r"\end{document}", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Tribunal
# ---------------------------------------------------------------------------


def judge_prompt(persona: str, paper: str, paper_budget: int = 24000) -> str:
    text = paper if len(paper) <= paper_budget else paper[:paper_budget] + "\n[...truncated for review...]"
    dims = ", ".join(DIMENSIONS)
    return textwrap.dedent(
        f"""
        You are {persona}. Review the paper below for a selective venue.

        Respond with ONE JSON object and nothing else, of the form:
        {{"scores": {{{', '.join(f'"{d}": <0-10>' for d in DIMENSIONS)}}}, "verdict": "<one sentence>", "improvements": ["<concrete change>", "<concrete change>", "<concrete change>"]}}

        Score each of {dims} from 0 (unacceptable) to 10 (exemplary). Be strict and specific.

        PAPER:
        {text}
        """
    ).strip()


def parse_judge_response(raw: str) -> Optional[Dict[str, Any]]:
    """Lenient JSON extraction. Returns None when no usable scores are present."""
    raw = strip_thinking(raw)
    match = _JSON_OBJECT.search(raw)
    if not match:
        return None
    candidate = match.group(0)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        # Trim to the outermost balanced object, a common failure is trailing prose.
        depth = 0
        for i, ch in enumerate(candidate):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        payload = json.loads(candidate[: i + 1])
                    except json.JSONDecodeError:
                        return None
                    break
        else:
            return None
    if not isinstance(payload, dict):
        return None
    scores_raw = payload.get("scores")
    if not isinstance(scores_raw, dict):
        scores_raw = {k: v for k, v in payload.items() if k in DIMENSIONS}
    scores: Dict[str, float] = {}
    for dim in DIMENSIONS:
        value = scores_raw.get(dim)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        scores[dim] = max(0.0, min(10.0, number))
    if not scores:
        return None
    improvements = payload.get("improvements") or []
    if isinstance(improvements, str):
        improvements = [improvements]
    return {
        "scores": scores,
        "verdict": str(payload.get("verdict") or "").strip(),
        "improvements": [str(i).strip() for i in improvements if str(i).strip()][:5],
    }


def recommendation(overall: float) -> str:
    if overall >= 8.5:
        return "accept: minor polish only"
    if overall >= 7.0:
        return "minor revision"
    return "major revision"


def aggregate_tribunal(judgements: Sequence[Dict[str, Any]], requested: int) -> Dict[str, Any]:
    per_dimension: Dict[str, float] = {}
    for dim in DIMENSIONS:
        values = [j["scores"][dim] for j in judgements if dim in j["scores"]]
        if values:
            per_dimension[dim] = round(statistics.fmean(values), 2)
    judge_means = [statistics.fmean(j["scores"].values()) for j in judgements]
    overall = round(statistics.fmean(judge_means), 2) if judge_means else 0.0
    median = round(statistics.median(judge_means), 2) if judge_means else 0.0
    improvements: List[str] = []
    for j in judgements:
        for item in j["improvements"]:
            if item not in improvements:
                improvements.append(item)
    return {
        "judges_requested": requested,
        "judges_counted": len(judgements),
        "overall": overall,
        "median": median,
        "lowest_judge": round(min(judge_means), 2) if judge_means else 0.0,
        "highest_judge": round(max(judge_means), 2) if judge_means else 0.0,
        "dimensions": per_dimension,
        "recommendation": recommendation(overall) if judgements else "no usable judgements",
        "verdicts": [j["verdict"] for j in judgements if j["verdict"]],
        "improvements": improvements[:10],
    }


def run_tribunal(
    chat: Callable[[str, str], str],
    paper: str,
    judges: int,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    judges = _judge_count(judges)
    system = "You are a rigorous peer reviewer. You answer only with the JSON object requested."
    paper = _REVIEW_HEADING.split(paper)[0]  # never review a previous review
    usable: List[Dict[str, Any]] = []
    for persona in JUDGE_PERSONAS[:judges]:
        if progress:
            progress(f"judge: {persona.split(' who ')[0]}")
        raw = chat(system, judge_prompt(persona, paper))
        parsed = parse_judge_response(raw)
        if parsed:
            usable.append(parsed)
    return aggregate_tribunal(usable, judges)


def render_tribunal_markdown(result: Dict[str, Any]) -> str:
    lines = [
        "## Tribunal Review",
        "",
        f"**Overall {result['overall']}/10** (median {result['median']}, range {result['lowest_judge']}–{result['highest_judge']}; "
        f"{result['judges_counted']} of {result['judges_requested']} judges returned usable scores). "
        f"Recommendation: **{result['recommendation']}**.",
        "",
        "| Dimension | Mean |",
        "|---|---|",
    ]
    for dim in DIMENSIONS:
        if dim in result["dimensions"]:
            lines.append(f"| {dim.replace('_', ' ')} | {result['dimensions'][dim]} |")
    if result["improvements"]:
        lines += ["", "**Suggested improvements**", ""]
        lines += [f"- {item}" for item in result["improvements"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _log(message: str) -> None:
    print(f"[cajal] {message}", file=sys.stderr, flush=True)


def _write_output(path: Optional[str], content: str, default_name: str) -> Path:
    target = Path(path) if path else Path.cwd() / default_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def build_client(args: argparse.Namespace) -> OllamaClient:
    host = resolve_setting(getattr(args, "host", None), "OLLAMA_HOST", DEFAULT_HOST)
    model = resolve_setting(getattr(args, "model", None), "CAJAL_MODEL", DEFAULT_MODEL)
    temperature = resolve_setting(getattr(args, "temperature", None), "CAJAL_TEMPERATURE", 0.3, float)
    return OllamaClient(host=host, model=model, temperature=temperature)


def _ensure_model(client: OllamaClient) -> None:
    if client.has_model(client.model):
        return
    available = client.models()
    hint = f" Available: {', '.join(available)}." if available else " No models are pulled yet."
    raise CajalError(
        f"Model {client.model!r} is not present on {client.host}.{hint} "
        f"Pull one (e.g. `ollama pull hf.co/Agnuxo/cajal-9b-v2-q4_k_m` then `--model hf.co/Agnuxo/cajal-9b-v2-q4_k_m`) "
        f"or pass --model / set CAJAL_MODEL to a listed model."
    )


def cmd_check(args: argparse.Namespace) -> int:
    client = build_client(args)
    report: Dict[str, Any] = {"host": client.host, "model": client.model}
    try:
        models = client.models()
        report["ollama"] = "ok"
        report["models"] = models
        report["model_present"] = client.has_model(client.model)
    except CajalError as exc:
        report["ollama"] = str(exc)
        report["model_present"] = False
    # arXiv is deliberately not probed here: a probe followed by a real query
    # inside arXiv's 3 s window is exactly what earns an HTTP 429.
    print(json.dumps(report, indent=2))
    return 0 if report.get("ollama") == "ok" and report.get("model_present") else 1


def cmd_references(args: argparse.Namespace) -> int:
    count = resolve_setting(args.count, "CAJAL_MIN_REFERENCES", DEFAULT_MIN_REFERENCES, _positive_int)
    refs = fetch_references(args.topic, count)
    if args.json:
        print(json.dumps(refs, indent=2))
    elif args.bibtex:
        print(render_bibtex(refs))
    else:
        print(render_reference_list(refs))
    return 0


def _read_context(path: Optional[str]) -> str:
    if not path:
        return ""
    target = Path(path)
    if not target.is_file():
        raise CajalError(f"context file not found: {target}")
    return target.read_text(encoding="utf-8-sig")


def _draft_single(args: argparse.Namespace, section: str) -> int:
    client = build_client(args)
    _ensure_model(client)
    refs: List[Dict[str, Any]] = []
    if not args.no_references and section != "abstract":
        count = resolve_setting(args.refs, "CAJAL_MIN_REFERENCES", DEFAULT_MIN_REFERENCES, _positive_int)
        refs = fetch_references(args.topic, count)
    context = _read_context(args.context)
    drafted = draft_sections(client.chat, args.topic, refs, context, sections=[section], progress=_log)
    audit = audit_citations(drafted[section], len(refs))
    output = f"## {SECTION_TITLES[section]}\n\n{audit['text']}\n"
    if refs:
        output += f"\n## References\n\n{render_reference_list(refs)}\n"
    if args.out:
        target = _write_output(args.out, output, f"{section}-{slugify(args.topic)}.md")
        _log(f"wrote {target}")
    else:
        print(output)
    return 0


def cmd_abstract(args: argparse.Namespace) -> int:
    return _draft_single(args, "abstract")


def cmd_methods(args: argparse.Namespace) -> int:
    return _draft_single(args, "methods")


def cmd_generate(args: argparse.Namespace) -> int:
    client = build_client(args)
    _ensure_model(client)
    refs: List[Dict[str, Any]] = []
    if not args.no_references:
        count = resolve_setting(args.refs, "CAJAL_MIN_REFERENCES", DEFAULT_MIN_REFERENCES, _positive_int)
        _log(f"fetching {count} arXiv references")
        refs = fetch_references(args.topic, count)
    context = _read_context(args.context)
    drafted = draft_sections(client.chat, args.topic, refs, context, progress=_log)
    audited: Dict[str, str] = {}
    removed: List[str] = []
    cited: set = set()
    for section, body in drafted.items():
        audit = audit_citations(body, len(refs))
        audited[section] = audit["text"]
        removed.extend(audit["removed"])
        cited.update(audit["cited"])
    audit_summary = {
        "cited": sorted(cited),
        "uncited": [n for n in range(1, len(refs) + 1) if n not in cited],
        "removed": removed,
    }
    if removed:
        _log(f"citation audit removed {len(removed)} bracket citation(s) that pointed outside the reference list")
    paper = assemble_markdown(args.topic, audited, refs, client.model, audit_summary, has_context=bool(context.strip()))
    tribunal: Optional[Dict[str, Any]] = None
    if not args.no_review:
        judges = resolve_setting(args.judges, "CAJAL_JUDGES", DEFAULT_JUDGES, _judge_count)
        _log(f"tribunal: {judges} judges")
        tribunal = run_tribunal(client.chat, paper, judges, progress=_log)
        paper += "\n" + render_tribunal_markdown(tribunal)
    fmt = (args.format or os.environ.get("CAJAL_FORMAT") or "markdown").lower()
    if fmt == "latex":
        content, suffix = markdown_to_latex(paper), ".tex"
    elif fmt == "markdown":
        content, suffix = paper, ".md"
    else:
        raise CajalError(f"unknown --format {fmt!r} (markdown or latex)")
    target = _write_output(args.out, content, f"paper-{slugify(args.topic)}{suffix}")
    summary = {
        "output": str(target),
        "model": client.model,
        "references": len(refs),
        "citation_audit": audit_summary,
        "tribunal": tribunal,
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    client = build_client(args)
    _ensure_model(client)
    draft_path = Path(args.draft)
    if not draft_path.is_file():
        raise CajalError(f"draft not found: {draft_path}")
    paper = draft_path.read_text(encoding="utf-8-sig")
    judges = resolve_setting(args.judges, "CAJAL_JUDGES", DEFAULT_JUDGES, _judge_count)
    result = run_tribunal(client.chat, paper, judges, progress=_log)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_tribunal_markdown(result))
    if args.append:
        stripped = _REVIEW_HEADING.split(paper)[0].rstrip() + "\n\n" + render_tribunal_markdown(result)
        draft_path.write_text(stripped, encoding="utf-8")
        _log(f"appended review to {draft_path}")
    return 0


def _add_model_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", help=f"Ollama base URL (env OLLAMA_HOST, default {DEFAULT_HOST})")
    parser.add_argument("--model", help=f"Ollama model name (env CAJAL_MODEL, default {DEFAULT_MODEL})")
    parser.add_argument("--temperature", type=float, help="sampling temperature (env CAJAL_TEMPERATURE, default 0.3)")


def _add_draft_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("topic", help="research topic, quoted")
    parser.add_argument("--refs", type=int, help=f"arXiv references to fetch (env CAJAL_MIN_REFERENCES, default {DEFAULT_MIN_REFERENCES})")
    parser.add_argument("--no-references", action="store_true", help="draft without fetching references (no citations)")
    parser.add_argument("--context", help="file with the author's notes / experimental results to draw from")
    parser.add_argument("--out", help="output file (default: ./paper-<topic>.md)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cajal_paper.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="verify Ollama is reachable and the model is present")
    _add_model_flags(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("references", help="fetch real arXiv references for a topic")
    p.add_argument("topic")
    p.add_argument("--count", type=int, help=f"how many (env CAJAL_MIN_REFERENCES, default {DEFAULT_MIN_REFERENCES})")
    p.add_argument("--bibtex", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_references)

    for name, func in (("abstract", cmd_abstract), ("methods", cmd_methods)):
        p = sub.add_parser(name, help=f"draft only the {SECTION_TITLES[name]} section")
        _add_draft_flags(p)
        _add_model_flags(p)
        p.set_defaults(func=func)

    p = sub.add_parser("generate", help="draft a full paper with citation audit and tribunal review")
    _add_draft_flags(p)
    _add_model_flags(p)
    p.add_argument("--format", choices=["markdown", "latex"], help="output format (env CAJAL_FORMAT, default markdown)")
    p.add_argument("--judges", type=int, help=f"tribunal size 1..{MAX_JUDGES} (env CAJAL_JUDGES, default {DEFAULT_JUDGES})")
    p.add_argument("--no-review", action="store_true", help="skip the tribunal review")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("review", help="tribunal review of an existing markdown draft")
    p.add_argument("draft")
    p.add_argument("--judges", type=int, help=f"tribunal size 1..{MAX_JUDGES} (env CAJAL_JUDGES, default {DEFAULT_JUDGES})")
    p.add_argument("--json", action="store_true")
    p.add_argument("--append", action="store_true", help="append the review to the draft file")
    _add_model_flags(p)
    p.set_defaults(func=cmd_review)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except CajalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
