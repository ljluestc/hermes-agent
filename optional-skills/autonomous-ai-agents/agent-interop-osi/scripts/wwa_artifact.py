#!/usr/bin/env python3
"""Project Hermes's live RoomLink catalog into Works With Agents interop artifacts.

    python wwa_artifact.py manifest CATALOG.json --agent-id hermes-dixie [--metrics M.json] [--out F]
    python wwa_artifact.py handoff CONTEXT.json --task-id T --sender A [--attempt N] [--catalog C.json]
    python wwa_artifact.py check ARTIFACT.json

CATALOG.json is the object served by ``GET /v1/room-members/capabilities`` (the whole
response or just its ``catalog``), built by ``gateway/hosted_room_peer.py::catalog_mapping``.
Nothing is invented: capabilities come from ``execution_policy.enabled_toolsets``, success
rates and durations only from ``--metrics``, and both source digests are carried into
``provenance`` so a reader can re-verify an artifact against the live catalog. Output is
deterministic — same input, byte-identical JSON — so a manifest can be diffed across runs.

Exit codes: 0 ok, 2 invalid input.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

MANIFEST_VERSION = "1.0.0"
# Fixed namespace: a replayed handoff of the same (task, sender, attempt) gets the same id,
# which is what makes it recognisable as a duplicate and reusable as an idempotency key.
HANDOFF_NAMESPACE = uuid.UUID("6f1d7f2a-2c1e-5b53-9a2f-0d5f6c8b4a11")
METRIC_RANGES = {"success_rate": (0.0, 1.0), "avg_duration_seconds": (0.0, None),
                 "max_concurrent": (1, None)}


class ArtifactError(ValueError):
    pass


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactError(f"{name} must be a number")
    if value < low or (high is not None and value > high):
        raise ArtifactError(f"{name} must be in [{low}, {high if high is not None else '∞'}]")
    return value


def _text(mapping, key, where):
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ArtifactError(f"{where} needs a non-empty string {key!r}")
    return value


def normalize_catalog(raw):
    """Accept the capabilities response or a bare catalog; return the catalog mapping."""
    if not isinstance(raw, dict):
        raise ArtifactError("catalog must be a JSON object")
    catalog = raw.get("catalog") if isinstance(raw.get("catalog"), dict) else raw
    policy = catalog.get("execution_policy")
    if not isinstance(policy, dict):
        raise ArtifactError("catalog needs an 'execution_policy' object")
    _text(catalog, "installation_id", "catalog")
    _text(catalog, "catalog_digest", "catalog")
    _text(policy, "target_profile", "execution_policy")
    _text(policy, "policy_digest", "execution_policy")
    toolsets = policy.get("enabled_toolsets")
    if not isinstance(toolsets, list) or not toolsets or not all(
            isinstance(t, str) and t.strip() for t in toolsets):
        raise ArtifactError("execution_policy.enabled_toolsets must be a non-empty list of names")
    if len(set(toolsets)) != len(toolsets):
        raise ArtifactError("execution_policy.enabled_toolsets has duplicates")
    # The gateway refuses to advertise a catalog for remote execution under YOLO mode
    # (hosted_room_peer.catalog_mapping); a manifest must not claim what it would refuse.
    if policy.get("approval_mode") == "off":
        raise ArtifactError("approval_mode 'off' cannot be published: remote runs need manual or smart approvals")
    _number(policy.get("max_iterations"), "execution_policy.max_iterations", 1, None)
    return catalog


def validate_metrics(metrics, toolsets):
    if not isinstance(metrics, dict):
        raise ArtifactError("metrics must be a JSON object keyed by toolset")
    unknown = sorted(set(metrics) - set(toolsets))
    if unknown:
        raise ArtifactError(f"metrics name toolsets this catalog does not enable: {unknown}")
    for toolset, values in metrics.items():
        if not isinstance(values, dict):
            raise ArtifactError(f"metrics[{toolset!r}] must be an object")
        extra = sorted(set(values) - set(METRIC_RANGES))
        if extra:
            raise ArtifactError(f"metrics[{toolset!r}] has unsupported keys: {extra}")
        for key, value in values.items():
            low, high = METRIC_RANGES[key]
            _number(value, f"metrics[{toolset!r}].{key}", low, high)
    return metrics


def build_manifest(catalog, agent_id, metrics=None):
    """Layer-3 Agent Capability Manifest derived from a RoomLink catalog."""
    if not isinstance(agent_id, str) or not agent_id.strip() or agent_id.split() != [agent_id]:
        raise ArtifactError("agent-id must be a non-empty string without whitespace")
    policy = catalog["execution_policy"]
    toolsets = list(policy["enabled_toolsets"])
    measured = validate_metrics(metrics or {}, toolsets)
    endpoint = catalog.get("endpoint") if isinstance(catalog.get("endpoint"), dict) else {}
    available = bool(endpoint.get("available"))
    return {
        "manifest_version": MANIFEST_VERSION,
        "agent_id": agent_id,
        "agent_type": "hermes",
        "capabilities": [
            {"action": "invoke", "target": toolset, **measured.get(toolset, {})}
            for toolset in sorted(toolsets)
        ],
        "resources": {"required_tools": sorted(toolsets)},
        "endpoint": {
            "protocol": "hermes-roomlink",
            "available": available,
            "address": endpoint.get("url") if available else None,
            "transport_security": endpoint.get("transport_security") if available else None,
            "unavailable_reason": None if available else endpoint.get("reason", "not_configured"),
        },
        "status": {"state": "healthy" if available else "offline"},
        "provenance": {
            "source": "hermes.room_member.capabilities",
            "installation_id": catalog["installation_id"],
            "target_profile": policy["target_profile"],
            "approval_mode": policy.get("approval_mode"),
            "max_iterations": policy["max_iterations"],
            "protocol_versions": catalog.get("protocol_versions", []),
            "link_modes": catalog.get("link_modes", []),
            "persistent_process": bool(catalog.get("persistent_process")),
            "catalog_digest": catalog["catalog_digest"],
            "policy_digest": policy["policy_digest"],
        },
    }


def handoff_id(task_id, sender, attempt):
    return str(uuid.uuid5(HANDOFF_NAMESPACE, f"{task_id}|{sender}|{attempt}"))


def build_handoff(context, task_id, sender, attempt=1, catalog=None):
    """Layer-4 Handoff Request with a replay-stable id, gated on the receiver's toolsets."""
    for name, value in (("task-id", task_id), ("sender", sender)):
        if not isinstance(value, str) or not value.strip():
            raise ArtifactError(f"{name} must be a non-empty string")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ArtifactError("attempt must be an integer >= 1")
    if not isinstance(context, dict):
        raise ArtifactError("context must be a JSON object")
    _text(context, "task_description", "context")
    required = context.get("tools_required", [])
    if not isinstance(required, list) or not all(isinstance(t, str) and t.strip() for t in required):
        raise ArtifactError("context.tools_required must be a list of toolset names")
    checklist = context.get("quality_checklist", [])
    if not isinstance(checklist, list) or not all(isinstance(c, str) for c in checklist):
        raise ArtifactError("context.quality_checklist must be a list of strings")
    body = {k: v for k, v in context.items() if k != "quality_checklist"}
    if catalog is not None:
        offered = set(catalog["execution_policy"]["enabled_toolsets"])
        missing = sorted(set(required) - offered)
        if missing:
            raise ArtifactError(f"receiver rejects: missing_tools {missing}")
    return {
        "handoff_id": handoff_id(task_id, sender, attempt),
        "task_id": task_id,
        "attempt": attempt,
        "sender": {"agent_id": sender},
        "context": body,
        "quality_checklist": list(checklist),
    }


def check(artifact):
    """Re-validate an artifact this script produced; returns its kind."""
    if not isinstance(artifact, dict):
        raise ArtifactError("artifact must be a JSON object")
    if "manifest_version" in artifact:
        provenance = artifact.get("provenance")
        if not isinstance(provenance, dict):
            raise ArtifactError("manifest needs a 'provenance' object")
        for key in ("catalog_digest", "policy_digest", "target_profile"):
            _text(provenance, key, "provenance")
        if provenance.get("approval_mode") == "off":
            raise ArtifactError("manifest advertises approval_mode 'off'")
        capabilities = artifact.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities:
            raise ArtifactError("manifest needs a non-empty 'capabilities' list")
        for capability in capabilities:
            if not isinstance(capability, dict):
                raise ArtifactError("each capability must be an object")
            _text(capability, "target", "capability")
            for key, (low, high) in METRIC_RANGES.items():
                if key in capability:
                    _number(capability[key], f"capability[{capability['target']!r}].{key}", low, high)
        status = artifact.get("status") if isinstance(artifact.get("status"), dict) else {}
        if status.get("state") not in {"healthy", "degraded", "busy", "stuck", "offline"}:
            raise ArtifactError("status.state is not a spec state")
        return "manifest"
    if "handoff_id" in artifact:
        sender_block = artifact.get("sender") if isinstance(artifact.get("sender"), dict) else {}
        task_id, sender = artifact.get("task_id"), sender_block.get("agent_id")
        attempt = artifact.get("attempt", 1)
        if artifact["handoff_id"] != handoff_id(task_id, sender, attempt):
            raise ArtifactError("handoff_id does not match task_id/sender/attempt — replays are not detectable")
        context = artifact.get("context") if isinstance(artifact.get("context"), dict) else {}
        _text(context, "task_description", "context")
        return "handoff"
    raise ArtifactError("artifact is neither a manifest nor a handoff request")


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(payload, out):
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n", encoding="utf-8")
    print(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Works With Agents interop artifacts from Hermes state.")
    sub = parser.add_subparsers(dest="mode", required=True)

    manifest = sub.add_parser("manifest", help="Layer-3 capability manifest from a RoomLink catalog")
    manifest.add_argument("catalog", type=Path)
    manifest.add_argument("--agent-id", required=True)
    manifest.add_argument("--metrics", type=Path, help="measured per-toolset metrics JSON")
    manifest.add_argument("--out", type=Path)

    handoff = sub.add_parser("handoff", help="Layer-4 handoff request with a replay-stable id")
    handoff.add_argument("context", type=Path)
    handoff.add_argument("--task-id", required=True)
    handoff.add_argument("--sender", required=True)
    handoff.add_argument("--attempt", type=int, default=1)
    handoff.add_argument("--catalog", type=Path, help="receiver catalog; rejects on missing_tools")
    handoff.add_argument("--out", type=Path)

    checker = sub.add_parser("check", help="re-validate a manifest or handoff request")
    checker.add_argument("artifact", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.mode == "manifest":
            catalog = normalize_catalog(_load(args.catalog))
            metrics = _load(args.metrics) if args.metrics else None
            _emit(build_manifest(catalog, args.agent_id, metrics), args.out)
        elif args.mode == "handoff":
            receiver = normalize_catalog(_load(args.catalog)) if args.catalog else None
            payload = build_handoff(_load(args.context), args.task_id, args.sender,
                                    args.attempt, receiver)
            _emit(payload, args.out)
        else:
            print(json.dumps({"kind": check(_load(args.artifact)), "ok": True}))
    except (OSError, json.JSONDecodeError, ArtifactError) as exc:
        print(f"wwa_artifact: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
