"""Behaviour of the signing core: round-trip, tamper detection, and key trust."""

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("agent_identity_core", Path(__file__).parents[1] / "identity.py")
identity = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = identity
_spec.loader.exec_module(identity)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*_a, **_k):
        raise AssertionError("agent-identity must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


@pytest.fixture
def key():
    return identity.load_private_key(identity.generate_seed())


def test_signed_envelope_verifies_with_signers_public_key(key):
    signed = identity.sign("researcher", {"type": "heartbeat", "ok": True}, key, now=1)

    assert signed["message"] == {"agent_id": "researcher", "timestamp": 1, "payload": {"type": "heartbeat", "ok": True}}
    assert identity.verify(signed, identity.public_key_hex(key))


@pytest.mark.parametrize("field, value", [("agent_id", "impostor"), ("timestamp", 2), ("payload", {"type": "x"})])
def test_any_change_to_the_envelope_breaks_the_signature(key, field, value):
    signed = identity.sign("researcher", {"type": "heartbeat"}, key, now=1)
    signed["message"][field] = value

    assert not identity.verify(signed, identity.public_key_hex(key))


def test_embedded_public_key_is_never_trusted(key):
    attacker = identity.load_private_key(identity.generate_seed())
    forged = identity.sign("researcher", {"type": "heartbeat"}, attacker)

    assert forged["public_key"] == identity.public_key_hex(attacker)
    assert not identity.verify(forged, identity.public_key_hex(key))


@pytest.mark.parametrize("doc", [{}, {"message": {}, "signature": "zz"}, {"message": "x", "signature": "00" * 64}])
def test_malformed_documents_are_rejected_not_raised(key, doc):
    assert not identity.verify(doc, identity.public_key_hex(key))


def test_seed_round_trips_to_the_same_identity():
    seed = identity.generate_seed()

    assert identity.public_key_hex(identity.load_private_key(seed)) == identity.public_key_hex(
        identity.load_private_key(seed.upper())
    )
    with pytest.raises(ValueError):
        identity.load_private_key(seed[:-2])
