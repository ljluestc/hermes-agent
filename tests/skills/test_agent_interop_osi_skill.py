"""agent-interop-osi: wwa_artifact.py derives its manifest from a REAL RoomLink catalog (built by
gateway.hosted_room_peer.catalog_mapping, not a fixture) and a handoff id is stable across replays
of one attempt. Runs the real script as a subprocess, offline."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[2]
          / "optional-skills/autonomous-ai-agents/agent-interop-osi/scripts/wwa_artifact.py")
CONTEXT = {"task_description": "Finish the compliance report",
           "tools_required": ["terminal", "file"],
           "quality_checklist": ["Audit trail is complete"]}


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def catalog(tmp_path):
    """A catalog from the production builder, so the projection tracks the real field names."""
    from gateway.hosted_room_peer import catalog_mapping
    value = catalog_mapping(installation_id="test-install", persistent_process=True,
                            target_profile="default")
    path = tmp_path / "catalog.json"
    # The gateway serves the catalog wrapped in a capabilities response; both shapes must work.
    path.write_text(json.dumps({"object": "hermes.room_member.capabilities", "catalog": value}),
                    encoding="utf-8")
    return path, value


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "ctx.json"
    path.write_text(json.dumps(CONTEXT), encoding="utf-8")
    return path


def test_manifest_mirrors_the_live_catalog_and_revalidates(catalog, context, tmp_path):
    path, value = catalog
    policy = value["execution_policy"]
    out = tmp_path / "nested" / "manifest.json"
    proc = _run("manifest", path, "--agent-id", "hermes-default", "--out", out)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads(proc.stdout)

    assert [c["target"] for c in manifest["capabilities"]] == sorted(policy["enabled_toolsets"])
    assert all(c["action"] == "invoke" and "success_rate" not in c for c in manifest["capabilities"])
    assert manifest["provenance"]["catalog_digest"] == value["catalog_digest"]
    assert manifest["provenance"]["policy_digest"] == policy["policy_digest"]
    assert manifest["provenance"]["target_profile"] == policy["target_profile"]
    assert manifest["status"]["state"] == ("healthy" if value["endpoint"].get("available") else "offline")
    assert json.loads(out.read_text(encoding="utf-8")) == manifest
    assert json.loads(_run("check", out).stdout) == {"kind": "manifest", "ok": True}


def test_handoff_id_is_stable_per_attempt_and_tamper_evident(catalog, context, tmp_path):
    path, _ = catalog
    args = ("handoff", context, "--task-id", "task-42", "--sender", "hermes-a", "--catalog", path)
    first, again = _run(*args), _run(*args)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout) == json.loads(again.stdout)

    bumped = _run(*args, "--attempt", "2")
    assert bumped.returncode == 0, bumped.stderr
    assert json.loads(bumped.stdout)["handoff_id"] != json.loads(first.stdout)["handoff_id"]

    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps({**json.loads(first.stdout), "task_id": "task-99"}), encoding="utf-8")
    assert _run("check", edited).returncode == 2


def test_receiver_gate_rejects_toolsets_the_catalog_does_not_offer(catalog, tmp_path):
    path, _ = catalog
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({**CONTEXT, "tools_required": ["terminal", "no_such_toolset"]}),
                   encoding="utf-8")
    proc = _run("handoff", ctx, "--task-id", "t", "--sender", "a", "--catalog", path)
    assert proc.returncode == 2
    assert "missing_tools" in proc.stderr and "no_such_toolset" in proc.stderr


@pytest.mark.parametrize("policy_patch, extra", [
    # A YOLO-mode policy is what the gateway itself refuses to advertise; publishing it is worse.
    ({"approval_mode": "off"}, ()),
    ({"enabled_toolsets": []}, ()),
    ({"policy_digest": ""}, ()),
    ({}, ("--agent-id", "two words")),
])
def test_untrue_or_unpublishable_input_is_rejected(catalog, tmp_path, policy_patch, extra):
    _, value = catalog
    patched = {**value, "execution_policy": {**value["execution_policy"], **policy_patch}}
    path = tmp_path / "patched.json"
    path.write_text(json.dumps(patched), encoding="utf-8")
    out = tmp_path / "manifest.json"
    args = ("manifest", path, *(extra or ("--agent-id", "hermes-default")), "--out", out)
    proc = _run(*args)
    assert proc.returncode == 2 and proc.stderr.startswith("wwa_artifact:")
    assert not out.exists()


def test_metrics_must_match_the_catalog_and_stay_in_range(catalog, tmp_path):
    path, value = catalog
    enabled = value["execution_policy"]["enabled_toolsets"][0]
    for metrics, ok in (({enabled: {"success_rate": 0.93}}, True),
                        ({enabled: {"success_rate": 1.5}}, False),
                        ({"no_such_toolset": {"success_rate": 0.5}}, False),
                        ({enabled: {"made_up_key": 1}}, False)):
        metrics_path = tmp_path / "metrics.json"
        metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
        proc = _run("manifest", path, "--agent-id", "hermes-default", "--metrics", metrics_path)
        assert (proc.returncode == 0) is ok, proc.stderr or proc.stdout
        if ok:
            measured = next(c for c in json.loads(proc.stdout)["capabilities"] if c["target"] == enabled)
            assert measured["success_rate"] == 0.93
