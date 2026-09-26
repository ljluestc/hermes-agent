# Agent OSI layers → Hermes surfaces

Per-layer answer to "does Hermes do this?" — **yes**, **partial** (a different mechanism covers
the need), or **no** (absent; say so). Every "yes" and "partial" names a surface that exists in
this repo today. Layer numbering follows the Works With Agents Agent OSI Model 1.0.0.

## Layer 1 — Runtime / Infrastructure

| Need | Status | Hermes surface |
|---|---|---|
| Run an agent as a reachable service | yes | `hermes serve`, `hermes gateway run`, the `api_server` platform |
| Declarative deployment | partial | Docker images and `hermes profile` distributions, not a WWA Deployment Manifest |
| Execution environments | yes | `tools/environments/` — local, docker, ssh, modal, daytona, singularity |
| Local-first / offline | yes | local providers (ollama, llama.cpp, LM Studio, vLLM), SQLite session store, local skills |
| Health | yes | `hermes status`, `hermes doctor`, `hermes monitoring` |

## Layer 2 — Identity & Auth

| Need | Status | Hermes surface |
|---|---|---|
| Authenticate a remote caller | yes | `API_SERVER_KEY` bearer, and `hermesroom` grants (HMAC-signed claims, scoped by permission) |
| Scoped, revocable, expiring authority | yes | `/v1/room-members/grants/refresh` and `/grants/revoke`, `ttl_seconds`, revocation checked per call |
| Tamper-evident authority | yes | grant claims pin `execution_policy_digest`; a changed policy forces reauthorization |
| Authorize a human | yes | `hermes pairing` DM pairing codes, per-platform allowed-user gates |
| DID / Ed25519 agent identity | **no** | grants are HMAC over a local secret; there is no DID, no public key identity, no signature chain |
| Agent onboarding to a network | **no** | peers are registered by hand with `hermes peer add` |

## Layer 3 — Discovery

| Need | Status | Hermes surface |
|---|---|---|
| Machine-readable capability declaration | yes | the RoomLink catalog — `gateway/hosted_room_peer.py::catalog_mapping`, served at `GET /v1/room-members/capabilities` |
| Digest-pinned, per-profile truth | yes | `catalog_digest` + `execution_policy.policy_digest`, built for the served `target_profile` |
| Finer-grained capability list | yes | `hermes skills list --enabled-only`, `hermes tools list`, `hermes mcp` |
| WWA Capability Manifest shape | via skill | `scripts/wwa_artifact.py manifest` projects the catalog; it does not replace it |
| Public registry / lookup service | **no** | the peer list is local and private |
| Computational trust score | **no** | absent |

## Layer 4 — Session

| Need | Status | Hermes surface |
|---|---|---|
| Transfer an in-progress task | partial | `hermes peer run` with a context pack; kanban tasks between profiles |
| Verifiable acceptance | yes | `hermes peer run` returns a run id; `hermes peer status` carries status and final output |
| Idempotent replay | yes | `--idempotency-key` with a durable reservation store (`gateway/platforms/api_server_run_idempotency.py`) |
| Context pack | yes | `hermes sessions export --format jsonl`, `session_search`, checkpoints |
| Continue a session | yes | `--resume`, `--continue`, `hermes sessions browse` |
| Clarification round-trip | partial | `clarify` asks the **user**; agent→agent questions ride the `hermes peer dm` reply channel |
| Context overflow handling | yes | context compression (`agent/` compression phases) rather than a rejection code |

## Layer 5 — Coordination

| Need | Status | Hermes surface |
|---|---|---|
| Delegate a subtask | yes | `delegate_task` (isolated context, same session) |
| Multi-worker task board | yes | kanban — `kanban_create`, `kanban_link`, `kanban_block`, `kanban_heartbeat`, `kanban_request_review` |
| Scheduled / recurring coordination | yes | `cronjob_manage`, `hermes cron`, `hermes webhook` |
| Async agent↔agent messaging | yes | `hermes peer dm` (sync reply) and `hermes peer run` (async run id) |
| Authority delegation with limits | yes | room grants scope permission and pin `max_iterations` + `approval_mode` |
| Fault tolerance / rollback | partial | `hermes checkpoints`, `kanban_block`, run statuses — no dead-letter queue |
| Ephemeral TTL-bound channels | partial | grant `ttl_seconds`; no separate ECP transport |

## Layer 6 — Presentation / Payload

| Need | Status | Hermes surface |
|---|---|---|
| Per-channel message formatting | yes | `gateway/platforms/` adapters (~20 platforms), `hermes send` |
| Tool payload schemas | yes | `model_tools.py` schemas, MCP client for external servers |
| MCP UI payload schema | **no** | not implemented |

## Layer 7 — Governance & Audit

| Need | Status | Hermes surface |
|---|---|---|
| Machine-enforceable policy | yes | `approvals.deny` globs (enforced under `/yolo`), `hooks.pre_tool_call` with `fail_closed` |
| Policy check before acting | yes | `hermes approvals test -- <command>` (0 allow, 2 ask, 3 deny), `hermes hooks doctor` |
| Policy enforcement point for traffic | partial | `hermes egress` credential-injection firewall; not a general AI-gateway PEP |
| Audit trail | yes | `hermes sessions export --format jsonl`, `hermes logs`, `hermes insights` |
| Emergency stop | yes | `hermes pause` / `hermes resume` |
| Supply-chain audit | yes | `hermes security` (OSV.dev over venv, plugins, MCP servers), pinned dependencies |
| Vulnerability disclosure | yes | `SECURITY.md` (repo policy, not a protocol endpoint) |
| Skill packaging format | partial | `SKILL.md` frontmatter + `hermes skills publish`; not ASFS |
| SLA, attestation, reputation ledger, auditor verification | **no** | absent |
| Agent commerce (ATP, AP2 mandate, economics) | **no** | absent; `hermes usage` observes the user's own spend only |

## The one refusal to remember

`catalog_mapping` raises rather than advertise a catalog whose `approval_mode` is `off`: a remote
RoomLink run always passes through manual or smart approvals, because process-wide YOLO mode
bypasses the scoped approval ContextVar and rewriting the advertised policy cannot make that safe.
`scripts/wwa_artifact.py` mirrors the refusal so a published manifest cannot claim otherwise.
