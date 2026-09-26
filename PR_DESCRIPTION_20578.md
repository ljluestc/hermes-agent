# feat(skills): agent-interop-osi — the Agent OSI model mapped onto Hermes surfaces

Refs #20578

## Summary

#20578 proposes adopting the [Works With Agents](https://github.com/workswithagents/specs) **Agent
OSI Model** — a 7-layer reference architecture over 28 interop specs — and points at single-file
reference agents in 6 languages plus a `workswithagents` SDK.

The vocabulary is the useful part, and the issue's own framing is right: Hermes already implements
most of these layers. It turns out to implement more than the issue claims — the gateway already
serves a **digest-pinned capability catalog** with a signed execution policy
(`gateway/hosted_room_peer.py::catalog_mapping`, `GET /v1/room-members/capabilities`) and already
does **idempotent cross-machine task transfer** (`hermes peer run --idempotency-key`, backed by
`gateway/platforms/api_server_run_idempotency.py`). What was missing was not a protocol; it was a
way for the agent to know which surface answers which interop question, and to hand a spec-shaped
artifact to a framework that speaks WWA rather than RoomLink.

This PR delivers that as an **optional skill** plus one stdlib-only helper that *projects* real
Hermes state into WWA artifacts. No vendored reference agents, no SDK dependency, no core change.

```bash
hermes skills install official/autonomous-ai-agents/agent-interop-osi
```

## Why a skill and not core protocol code

| Rule (root `AGENTS.md`) | The issue as written | This PR |
|---|---|---|
| Footprint Ladder | reference agents + SDK + per-layer protocol code | Rung 2: optional skill driving existing `hermes` commands and tools |
| Third-party products don't land in the core tree | `pip install workswithagents`, 6 in-tree reference agents | Artifacts are plain JSON; the helper is stdlib-only; nothing is added to `pyproject.toml` |
| No speculative infrastructure | protocol layers with no in-tree consumer | No new hooks, ABCs, registries, config keys or env vars |
| Extend, don't duplicate | a new capability-manifest source of truth | The RoomLink catalog stays the source of truth; the manifest is a derived projection carrying its digests |
| No new core tool when terminal + skill suffice | new interop tools | `terminal` + `read_file`/`write_file` + one script |
| Never hardcode `~/.hermes` | — | Artifacts live under `"$HERMES_HOME/interop/"` |

## Layer → surface map

The full table (with per-spec **yes / partial / no**) ships as `references/osi-layer-map.md`.

| Layer | Hermes surface | Status |
|---|---|---|
| 1 — Runtime | `hermes serve`, `tools/environments/` (local, docker, ssh, modal, daytona, singularity), local providers, `hermes status` / `doctor` | implemented; no WWA deployment manifest |
| 2 — Identity & Auth | `hermesroom` grants — HMAC-signed claims, scoped by permission, TTL, refresh/revoke, `execution_policy_digest` pinned; `API_SERVER_KEY`; `hermes pairing` | implemented differently — **no DID / Ed25519 agent identity**, no onboarding to a network |
| 3 — Discovery | `catalog_mapping` → `GET /v1/room-members/capabilities`; `hermes skills list`, `hermes tools list`, `hermes mcp` | implemented; **no public registry, no trust score** |
| 4 — Session | `hermes peer run --idempotency-key` + durable reservation store; `hermes sessions export`, `--resume`, checkpoints; context compression | implemented; `clarify` is agent→**human**, agent→agent questions ride the `hermes peer dm` reply |
| 5 — Coordination | `delegate_task`, kanban (`kanban_create` / `kanban_link` / `kanban_block` / `kanban_heartbeat`), `cronjob_manage`, `hermes peer dm` / `run` / `status` / `stop` | implemented; no dead-letter queue |
| 6 — Presentation | `gateway/platforms/` adapters (~20 platforms), `hermes send`, `model_tools.py` schemas | implemented; no MCP UI payload schema |
| 7 — Governance & Audit | `approvals.deny`, `hooks.pre_tool_call` with `fail_closed`, `hermes approvals test`, `hermes security` (OSV), `hermes sessions export --format jsonl`, `hermes logs`, `hermes pause` | implemented; **no SLA, attestation, reputation ledger, ATP/AP2 commerce** |

The absent layers are written down as absent. The skill's Pitfalls section tells the agent to say
so rather than emit an empty section that reads like support.

## What changed

| Path | Change |
|---|---|
| `optional-skills/autonomous-ai-agents/agent-interop-osi/SKILL.md` | New skill, 168 lines. Modern section order; four procedures (publish a manifest, hand work off, coordinate, answer a conformance question), each with a Verification step |
| `…/scripts/wwa_artifact.py` | Stdlib-only helper, 3 subcommands — `manifest`, `handoff`, `check`. Details below |
| `…/references/osi-layer-map.md` | The per-layer, per-spec yes/partial/no table |
| `tests/skills/test_agent_interop_osi_skill.py` | 5 tests (8 cases) running the real script against a catalog built by the **real** `catalog_mapping` |
| `website/docs/reference/optional-skills-catalog.md`, `website/sidebars.ts`, `website/docs/user-guide/skills/optional/autonomous-ai-agents/autonomous-ai-agents-agent-interop-osi.md` | Output of `website/scripts/generate-skill-docs.py` (+1 line in each listing file, plus the new page) |

No core Python, config keys, env vars, tools, hooks or dependencies were added.

### What the helper does — and refuses to do

`wwa_artifact.py manifest CATALOG.json --agent-id <id> [--metrics M.json]` reads the object served
by `GET /v1/room-members/capabilities` (or a bare catalog) and emits a Layer-3 Agent Capability
Manifest. The point is that **nothing is invented**:

- `capabilities` comes from `execution_policy.enabled_toolsets`, sorted — not from the model's idea
  of what Hermes can do.
- `success_rate` / `avg_duration_seconds` / `max_concurrent` appear **only** from `--metrics`, and a
  metrics file naming a toolset the catalog does not enable, or an out-of-range value, exits 2. A
  manifest with no metrics is honest; an invented `0.94` is not.
- `catalog_digest` and `policy_digest` are carried into `provenance`, so a reader can re-verify the
  manifest against the live catalog instead of trusting it.
- `status.state` is derived (`healthy` when the endpoint is advertised, else `offline`).
- **It refuses to publish `approval_mode: "off"`** — mirroring `catalog_mapping`, which raises
  rather than advertise a YOLO-mode catalog for remote execution, because process-wide YOLO bypasses
  the scoped approval ContextVar and rewriting the advertised policy cannot make it safe.

`wwa_artifact.py handoff CONTEXT.json --task-id T --sender A [--attempt N] [--catalog C]` emits a
Layer-4 Handoff Request whose `handoff_id` is `uuid5(fixed_namespace, "task|sender|attempt")`. That
one decision buys two spec requirements at once: **idempotent replay** (a retry of the same attempt
produces the same id, so it doubles as the `hermes peer run --idempotency-key`) and **tamper
evidence** (`check` recomputes it, so an edited `task_id` fails). With `--catalog`, the receiver's
toolsets gate the handoff and it exits 2 with `missing_tools [...]`, which is the spec's own
rejection reason.

Output is deterministic (`sort_keys`, no timestamps), so a manifest can be diffed across runs.

## Design notes

- **Prompt-cache safe.** Optional skill: nothing enters the system prompt until the user installs
  it, and then only the 49-char description joins the skills index.
- **Profile-aware.** `enabled_toolsets` is per *served* profile, not per process — a multiplexed
  gateway advertises one catalog per profile. The skill says to pass the profile you mean and keeps
  `target_profile` in the artifact; paths go through `$HERMES_HOME`.
- **A manifest is not an authorization.** Called out explicitly in Pitfalls: the grant authorizes,
  a status-only grant cannot mint dispatch authority, and a refresh fails if the policy changed.
- **Every cited surface was checked against this tree**, not recalled — the catalog field names come
  from running `catalog_mapping`, the peer flags from `hermes peer --help`, the toolset names from
  `toolsets.py`.
- **The issue's links needed verifying.** `github.com/workswithagents/works-with-agents` 404s and
  `workswithagents.dev/specs/` returned 502; the live specs are at
  `github.com/workswithagents/specs`, which is what the layer map was written against. That is a
  further reason the artifacts are hand-rolled JSON rather than an SDK call.

## Honest gaps (possible focused follow-ups, not in this PR)

1. **No CLI for the catalog.** The skill builds it with a one-line `python -c` import of
   `catalog_mapping` when no grant is at hand. A `hermes gateway catalog --profile <p>` would be a
   small, honest addition on existing code — worth doing only if someone asks.
2. **Metrics are manual.** `hermes insights` has the data for per-toolset success rates; wiring it
   into `--metrics` would need a stable JSON output that does not exist yet (`hermes skills list`
   and `hermes tools list` have no `--json` either).
3. **Layer 2 stays HMAC.** DID/Ed25519 agent identity is a real gap, not a papered-over one. It is
   a core security change and does not belong in a skill PR.

## Tests

`tests/skills/test_agent_interop_osi_skill.py` runs the real script via `subprocess`, stdlib +
pytest only, no network. The catalog fixture calls the **production** `catalog_mapping`, so the
projection tracks real field names instead of a frozen fixture:

| Test | Contract |
|---|---|
| `test_manifest_mirrors_the_live_catalog_and_revalidates` | `capabilities` targets equal `sorted(enabled_toolsets)`; no metric keys without `--metrics`; `provenance` digests and `target_profile` equal the catalog's; `status.state` follows endpoint availability; `--out` matches stdout; `check` re-validates |
| `test_handoff_id_is_stable_per_attempt_and_tamper_evident` | Two identical invocations produce identical JSON; `--attempt 2` changes the id; `check` on an edited `task_id` exits 2 |
| `test_receiver_gate_rejects_toolsets_the_catalog_does_not_offer` | Exits 2 naming `missing_tools` and the offending toolset |
| `test_untrue_or_unpublishable_input_is_rejected` (×4) | `approval_mode: "off"`, empty `enabled_toolsets`, blank `policy_digest` and a whitespace `--agent-id` each exit 2 with a `wwa_artifact:` message and write no file |
| `test_metrics_must_match_the_catalog_and_stay_in_range` | A measured `success_rate` reaches the right capability; out-of-range, unknown-toolset and unknown-key metrics are rejected |

```
$ scripts/run_tests.sh tests/skills/test_agent_interop_osi_skill.py \
    tests/skills/test_authoring_standards.py tests/skills/test_optional_skill_self_paths.py \
    tests/skills/test_skill_docs_contract.py tests/skills/test_skill_document_contracts.py \
    tests/skills/test_skill_pages_match_shipped_skills.py
✓ tests/skills/test_agent_interop_osi_skill.py (8✓, 6.1s)
✓ tests/skills/test_authoring_standards.py (1464✓, 246.6s)
=== Summary: 6 files, 1510 tests passed, 0 failed, 1 skipped (100% complete) in 246.7s ===

$ ruff check optional-skills/autonomous-ai-agents/agent-interop-osi tests/skills/test_agent_interop_osi_skill.py
All checks passed!
```

### End-to-end: install through the real hub path into a temp `HERMES_HOME`

```
$ HERMES_HOME=$(mktemp -d) hermes skills install official/autonomous-ai-agents/agent-interop-osi --yes
Scan: agent-interop-osi (official/builtin)  Verdict: SAFE
Decision: ALLOWED — Allowed (builtin source, safe verdict)
Installed: autonomous-ai-agents/agent-interop-osi
Files: SKILL.md, references/osi-layer-map.md, scripts/wwa_artifact.py
```

### End-to-end: against a catalog from the real gateway builder

```
$ python -c "import json;from gateway.hosted_room_peer import catalog_mapping;print(json.dumps({'catalog':catalog_mapping(installation_id='inst-abc123',persistent_process=True,target_profile='default')}))" > catalog.json
$ python scripts/wwa_artifact.py manifest catalog.json --agent-id hermes-dixie --metrics metrics.json | head
{
  "agent_id": "hermes-dixie",
  "agent_type": "hermes",
  "capabilities": [
    {"action": "invoke", "target": "bot_room"},
    ...
    {"action": "invoke", "avg_duration_seconds": 12, "max_concurrent": 2, "success_rate": 0.93, "target": "terminal"},
$ python scripts/wwa_artifact.py check manifest.json
{"kind": "manifest", "ok": true}

$ python scripts/wwa_artifact.py handoff ctx.json --task-id task-42 --sender hermes-dixie --catalog catalog.json
  → handoff_id 39c3dddb-0737-55c5-94a1-12078bb50989   (identical on replay)
$ python scripts/wwa_artifact.py handoff ctx_missing_tool.json --task-id task-42 --sender hermes-dixie --catalog catalog.json
wwa_artifact: receiver rejects: missing_tools ['quantum_teleport']            # exit 2
$ python scripts/wwa_artifact.py manifest catalog_yolo.json --agent-id x
wwa_artifact: approval_mode 'off' cannot be published: remote runs need manual or smart approvals   # exit 2
$ python scripts/wwa_artifact.py check handoff_with_edited_task_id.json
wwa_artifact: handoff_id does not match task_id/sender/attempt — replays are not detectable        # exit 2
```

## Risk

Low. Opt-in optional skill, no core change, no new dependency. The only files outside the skill are
the generated docs and its test.

## Credit

The proposal and the Agent OSI framing are by @vystartasv in #20578. This PR maps its layers onto
the surfaces Hermes already ships and adds the projection helper, rather than vendoring the
reference implementations.
