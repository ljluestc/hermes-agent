---
name: agent-interop-osi
description: Map Agent OSI layers onto Hermes interop surfaces.
version: 1.0.0
author: Vilius Vystartas (vystartasv), ljluestc (ljluestc), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [interop, agent-osi, capability-manifest, handoff, delegation, roomlink, peer, governance, audit]
    category: autonomous-ai-agents
    related_skills: [agent-merge-conflict-arbiter, dynamic-workflow]
---

# Agent Interop OSI Skill

Answers "which Hermes surface is the one for talking to another agent?" using the Works With
Agents 7-layer Agent OSI model as the vocabulary, and projects Hermes's own live state into
spec-shaped artifacts other frameworks can read — a Layer-3 capability manifest and a Layer-4
handoff request, both built by `scripts/wwa_artifact.py` from the RoomLink catalog the gateway
already serves. It adds no protocol, no daemon and no dependency: every layer below resolves to
a surface Hermes ships today, and `references/osi-layer-map.md` names the layers it does not.

## When to Use

- Another agent or orchestrator asks what this Hermes can do, and wants a machine-readable answer.
- Work has to move from this agent to another one (another framework, another machine, another
  profile) with its context, and the transfer must survive a retry without running twice.
- The user is auditing Hermes against an interop standard and wants the honest per-layer answer,
  including the gaps.
- The user cites a WWA spec (Handoff, Capability Manifest, IACP, ACP, Delegation Framework,
  Compliance-as-Code) and asks whether Hermes implements it.

Skip it for a plain subagent fan-out inside one session: that is `delegate_task`, no envelope needed.

## Prerequisites

- `terminal` for `hermes ...` commands and `scripts/wwa_artifact.py` (Python 3.8+, stdlib only).
- `read_file` / `write_file` for the artifact JSON; `search_files` to locate a catalog response.
- Cross-machine work needs the `api_server` platform on the far side plus a registered peer
  (`hermes peer add`), or a RoomLink grant. Neither is set up by this skill — check first with
  `hermes peer list` and `hermes status`.
- Write artifacts under the profile's Hermes home, never a hardcoded home path: the local
  terminal exports `HERMES_HOME`, so use `"$HERMES_HOME/interop/"`.

## How to Run

Identify the layer the request lives at from the table below, run that layer's Procedure step,
and report real output. Never claim conformance with a spec the map marks as absent.

## Quick Reference

| Layer | Hermes surface | Key calls |
|---|---|---|
| 7 — Governance & Audit | approvals, shell hooks, session export, OSV audit | `hermes approvals test -- <cmd>`, `hermes security`, `hermes sessions export` |
| 6 — Presentation | gateway platform adapters | `hermes send`, `gateway/platforms/` |
| 5 — Coordination | delegation, kanban, cron, peer runs | `delegate_task`, `kanban_create`, `cronjob_manage`, `hermes peer run` |
| 4 — Session | peer runs with idempotency, session export/resume | `hermes peer run --idempotency-key`, `hermes peer status`, `--resume` |
| 3 — Discovery | RoomLink capability catalog, skills, toolsets | `GET /v1/room-members/capabilities`, `hermes skills list`, `hermes tools list` |
| 2 — Identity & Auth | `hermesroom` grants, API server key, DM pairing | `hermes peer add --key`, `hermes pairing`, grant refresh/revoke |
| 1 — Runtime | serve, terminal backends, local models | `hermes serve`, `hermes status`, `hermes doctor` |

## Procedure

### 1. Publish what this agent can do (Layer 3)

1. Get the live catalog. From a running gateway with the `api_server` platform, read
   `GET /v1/room-members/capabilities` with the room grant and save the response body. Without a
   grant, build the same mapping locally in one `terminal` call:

   ```bash
   python -c "import json;from gateway.hosted_room_peer import catalog_mapping;print(json.dumps(catalog_mapping(installation_id='local',persistent_process=True,target_profile='default')))" > "$HERMES_HOME/interop/catalog.json"
   ```

2. Project it into a WWA manifest. The script reads `execution_policy.enabled_toolsets` and
   carries `catalog_digest` and `policy_digest` into `provenance`, so a reader can re-verify the
   manifest against the live catalog:

   ```bash
   python scripts/wwa_artifact.py manifest "$HERMES_HOME/interop/catalog.json" --agent-id hermes-<profile> --out "$HERMES_HOME/interop/manifest.json"
   ```

3. Success rates and durations are optional and measured, never guessed. Supply them with
   `--metrics` as `{"terminal": {"success_rate": 0.93, "avg_duration_seconds": 12}}`, sourced from
   real runs — `hermes insights` and `session_search` are where those numbers come from. Omit the
   flag when nothing has been measured; a manifest with no metrics is honest, an invented 0.94 is not.
4. Skills and MCP servers are the finer-grained capability list behind the toolsets. When the
   caller needs that detail, add `hermes skills list --enabled-only` and `hermes tools list` output
   alongside the manifest rather than inflating `capabilities` with names the catalog never
   advertised.

### 2. Hand work to another agent (Layer 4)

1. Build the context pack as JSON with `write_file`: `task_description`, `workspace_path`,
   `state_snapshot` (branch, last commit, files touched), `agent_memory` (`key_decisions`,
   `discovered_pitfalls`, `pending_items`), `tools_required`, `constraints`, `quality_checklist`.
   Fill `discovered_pitfalls` from `session_search` on the task, not from memory of this turn.
2. Check the receiver can take it before sending. Pass its catalog so the gate is real; the
   script rejects with `missing_tools` exactly as the spec's error table requires:

   ```bash
   python scripts/wwa_artifact.py handoff "$HERMES_HOME/interop/ctx.json" --task-id <task> --sender hermes-<profile> --catalog "$HERMES_HOME/interop/receiver.json" --out "$HERMES_HOME/interop/handoff.json"
   ```

3. Deliver it. `handoff_id` is derived from `task_id`, sender and `attempt`, so a retry of the same
   attempt produces the same id — pass it straight through as the idempotency key and the far side
   recognises the duplicate instead of running the task twice:

   ```bash
   hermes peer run <peer> --idempotency-key <handoff_id> < "$HERMES_HOME/interop/handoff.json"
   hermes peer status <peer> <run_id>
   ```

4. Bump `--attempt` only for a genuinely new attempt (the context changed). Reusing attempt 1 with
   a new context pack and expecting a fresh run is the mistake the idempotency store will catch.
5. Between profiles on one machine, a kanban task is the handoff and needs no envelope:
   `kanban_create` with the context pack in the body, `kanban_link` to the original,
   `kanban_request_review` when done.

### 3. Coordinate several agents (Layer 5)

1. Inside one session, `delegate_task` — isolated context, no envelope, no network.
2. Across profiles on one machine, kanban tasks with `kanban_link` for dependencies, and
   `kanban_heartbeat` so a stalled worker is visible.
3. Across machines, `hermes peer dm` for a short question and `hermes peer run` for a long task
   (it returns a run id; poll `hermes peer status`, stop with `hermes peer stop`).
4. For recurring coordination, `cronjob_manage(action="create", ...)` with `skills` set to this
   skill so each run loads the map.

### 4. Answer a conformance question (Layers 2 and 7)

1. Read `references/osi-layer-map.md` and answer per layer: implemented, partial, or absent.
   State the mechanism, not the spec name — Hermes signs room grants with an HMAC secret and pins
   the execution policy by digest; it does not do DID or Ed25519 agent identity.
2. Governance claims are checkable, so check them: `hermes approvals test -- <command>` for the
   policy, `hermes hooks doctor` for the `pre_tool_call` gates, `hermes security` for the
   dependency audit, `hermes sessions export audit.jsonl --session-id <id> --format jsonl` for the
   audit trail.
3. Note the one refusal that matters for remote execution: the gateway will not advertise a
   catalog whose `approval_mode` is `off`, and `scripts/wwa_artifact.py` refuses to publish one
   either. A remote room run always goes through manual or smart approvals.

## Pitfalls

- Don't write a manifest by hand. Derived from the catalog, it stays true as toolsets change;
  hand-written, it starts wrong the first time someone runs `hermes tools disable`.
- `enabled_toolsets` is per served profile, not per process. A multiplexed gateway advertises one
  catalog per profile, so pass the profile you mean and keep `target_profile` in the artifact.
- Don't hardcode `~/.hermes`: profiles live elsewhere. Use `$HERMES_HOME`.
- A capability manifest is not an authorization. The grant is what authorizes; a status-only grant
  cannot mint dispatch authority, and refreshing one fails if the execution policy changed.
- Don't install the `workswithagents` SDK into the Hermes environment to satisfy a spec. The
  artifacts are plain JSON and this script is stdlib-only; Hermes dependency changes go through
  `hermes pm`, and a spec SDK is not a Hermes dependency.
- Absent layers stay absent. There is no public agent registry, no trust score, no payment
  mandate and no DID identity in Hermes — say so rather than emitting an empty section that
  reads like support.

## Verification

- Manifest: `python scripts/wwa_artifact.py check "$HERMES_HOME/interop/manifest.json"` prints
  `{"kind": "manifest", "ok": true}`, and its `provenance.catalog_digest` equals `catalog_digest`
  in the catalog it came from.
- Handoff gate: a context pack naming a toolset the receiver's catalog omits exits 2 with
  `missing_tools [...]`.
- Replay stability: running `handoff` twice with the same `--task-id`, `--sender` and `--attempt`
  prints the same `handoff_id`; `check` on an edited copy fails.
- Delivery: `hermes peer status <peer> <run_id>` shows one run for a re-sent idempotency key.
