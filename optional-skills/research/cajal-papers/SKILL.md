---
name: cajal-papers
description: "Draft and peer-review papers on a local Ollama model."
version: 1.0.0
author: Francisco Angulo de Lafuente (@Agnuxo1) + Hermes Agent
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  commands: [ollama]
metadata:
  hermes:
    tags: [Research, Papers, Academic, Ollama, Local LLM, Citations, arXiv, Peer Review]
    category: research
    homepage: https://github.com/Agnuxo1/CAJAL
    related_skills: [arxiv, grounded-citations, research-paper-writing, pdf]
    config:
      - key: cajal.ollama_host
        description: Base URL of the Ollama server that drafts and reviews papers
        default: "http://localhost:11434"
      - key: cajal.model
        description: Ollama model used for drafting and the tribunal (any pulled model works)
        default: "cajal"
      - key: cajal.min_references
        description: Number of real arXiv references fetched for each paper
        default: 8
---

# CAJAL Papers Skill

Draft a seven-section scientific paper, or a single section, on a model that runs
on the user's own Ollama server, then score it with a tribunal of LLM judges. The
paper's references are fetched from arXiv and the model may cite only those, so the
reference list is real by construction. This skill does not run experiments, does
not submit anywhere, and does not need the `cajal-p2pclaw` PyPI package; for the
full ML conference pipeline with LaTeX templates use `research-paper-writing`.

## When to Use

- "Write a paper / draft on <topic>", "give me an abstract for …", "draft the
  methods section for …" when the user wants it done locally or for free
- "Find me N real references on <topic>" when a numbered, verifiable list is wanted
- "Peer review this draft", "score my paper" for a quick multi-judge critique
- A user who mentions CAJAL, P2PCLAW, or `/cajal-papers`

Do not use it for literature search alone (`arxiv`) or for citing web sources in
chat answers (`grounded-citations`).

## Prerequisites

1. **Ollama** running and reachable: `ollama serve` (default `http://localhost:11434`).
2. **A model.** Any pulled model works; a CAJAL fine-tune is optional:

   ```bash
   ollama pull hf.co/Agnuxo/cajal-9b-v2-q4_k_m     # ~5.5 GB, CAJAL 9B paper model
   ollama cp hf.co/Agnuxo/cajal-9b-v2-q4_k_m cajal  # optional alias matching the default
   ```

   Or reuse whatever is already pulled (`qwen3`, `llama3.1`, …) by passing `--model`.
3. **Python 3.9+** with the standard library only. No pip installs.
4. **Network access to `export.arxiv.org`** for references; pass `--no-references`
   to draft offline (the paper then carries no citations).

Settings come from the `[Skill config]` block Hermes injects when the skill loads
(`cajal.ollama_host`, `cajal.model`, `cajal.min_references`). Pass them to the script
as `--host`, `--model`, `--refs`; the environment variables `OLLAMA_HOST`,
`CAJAL_MODEL`, `CAJAL_MIN_REFERENCES`, `CAJAL_JUDGES`, `CAJAL_FORMAT` work too.

## How to Run

```bash
S=~/.hermes/skills/research/cajal-papers/scripts/cajal_paper.py
python3 "$S" check --model qwen3                       # Ollama up? model present?
python3 "$S" references "Byzantine consensus" --count 12
python3 "$S" abstract "Neural architecture search for edge devices" --model qwen3
python3 "$S" methods "Federated learning with differential privacy" --model qwen3
python3 "$S" generate "Quantum error correction with surface codes" --model qwen3 --out paper.md
python3 "$S" review paper.md --judges 8 --append
```

Run every command through `terminal`. `generate` takes several minutes on a laptop
(seven drafting calls plus one call per judge); run it in the background when the
platform supports it and report progress from the `[cajal] …` lines on stderr.

## Quick Reference

| Task | Command |
|------|---------|
| Verify setup | `check [--host H] [--model M]` → JSON, exit 1 if the model is missing |
| Real references | `references "topic" [--count N] [--bibtex \| --json]` |
| One section | `abstract "topic"` or `methods "topic"` `[--refs N] [--context notes.md] [--out f]` |
| Full paper | `generate "topic" [--refs N] [--context notes.md] [--judges K] [--no-review] [--format latex] [--out f]` |
| Review a draft | `review draft.md [--judges K] [--json] [--append]` |
| Model flags | `--host`, `--model`, `--temperature` on every drafting command |

`generate` prints a JSON summary (output path, reference count, citation audit,
tribunal scores) on stdout; progress goes to stderr.

## Procedure

1. **Check the runtime first.** Run `check` with the configured model. If
   `model_present` is false, either pull the CAJAL model above or pick one of the
   listed `models` and pass it with `--model`; never guess a name.
2. **Collect what the user actually has.** Ask for notes, results, tables, or a
   draft and save them to a file; pass it with `--context`. Without it the Results
   section is written as an *evaluation plan*, clearly labelled, not as measurements.
3. **Fetch references early** with `references` when the user wants to vet them;
   `generate` fetches its own list otherwise. Only these papers can be cited.
4. **Generate.** Prefer the default eight references and eight judges; drop to
   `--judges 3` on slow hardware, or `--no-review` for a first look.
5. **Read the citation audit** in the JSON summary. `removed` lists bracket
   citations the model invented and the script stripped; `uncited` lists references
   the paper never used. Mention both to the user.
6. **Report the tribunal** (overall, weakest dimension, recommendation) and the
   output path. Offer to revise a section and re-run `review --append`.
7. **Deliver.** Use `read_file` to quote from the draft; hand over `.tex` output with
   `--format latex` when the user wants LaTeX. PDF is out of scope for this script; the
   `pdf` skill converts the markdown.

## Pitfalls

- **Results are not data.** No local model can produce measurements. Without
  `--context` the draft says so in a note under the title; do not remove it.
- **arXiv 429.** arXiv wants ~3 s between calls. The script retries with a back-off;
  if it still fails, wait a few seconds rather than hammering, or use `--no-references`.
- **The CAJAL 9B card asks the model to redirect paper requests to p2pclaw.com.**
  The system prompt forbids that; if a section still reads like a redirect, re-run
  with a lower temperature or another model.
- **Thinking models** (`qwen3`, `deepseek-r1`) are fine: the script sends `think: false`
  (dropping it for models that reject the flag), retries once with double the token
  budget if reasoning still ate the answer, and strips any inline `<think>` block.
- **Judges that return prose instead of JSON are dropped**; `judges_counted` in the
  summary says how many scores survived. Fewer than half means the model is too
  small for structured review, so try a larger one.
- **Model names.** `cajal` is only the default alias; nothing is pulled automatically
  and `check` is the place to see what the server actually has.
- **Remote Ollama hosts** are allowed via `--host`, but then the topic, notes, and
  draft leave the machine; say so if the user chose a remote host.

## Verification

- `check` returns `"ollama": "ok"` and `"model_present": true`.
- `references "transformer attention" --count 3` prints three `[n]` lines with
  `arXiv:` ids that resolve at `https://arxiv.org/abs/<id>`.
- `generate` writes a file with `## Abstract` … `## Conclusion`, a `## References`
  list identical to what `references` returned, and a `## Tribunal Review` table.
- Every bracket citation in the draft is ≤ the number of references; `removed`
  in the summary explains any that were dropped.
- Tests: `scripts/run_tests.sh tests/skills/test_cajal_papers_skill.py -q`.
