"""Local Ed25519 agent identity: key handling, signed envelopes, verification.

Pure functions with no Hermes imports so they can be tested and reused standalone.
The envelope shape (``agent_id`` / ``timestamp`` / ``payload``, canonical JSON with
sorted keys) follows the Works With Agents identity protocol, but verification is
done here with the public key instead of a remote registry.
"""

from __future__ import annotations

import json
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SEED_BYTES = 32


def generate_seed() -> str:
    """A fresh private key, as the hex seed stored in ``AGENT_IDENTITY_SIGNING_KEY``."""
    return Ed25519PrivateKey.generate().private_bytes_raw().hex()


def load_private_key(seed_hex: str) -> Ed25519PrivateKey:
    try:
        seed = bytes.fromhex(seed_hex.strip())
    except ValueError as exc:
        raise ValueError("signing key is not hex") from exc
    if len(seed) != SEED_BYTES:
        raise ValueError(f"signing key must be {SEED_BYTES} bytes ({SEED_BYTES * 2} hex chars), got {len(seed)}")
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key_hex(private_key: Ed25519PrivateKey) -> str:
    return private_key.public_key().public_bytes_raw().hex()


def canonical(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def identity_card(agent_id: str, private_key: Ed25519PrivateKey) -> dict[str, Any]:
    return {"agent_id": agent_id, "public_key": public_key_hex(private_key), "algorithm": "ed25519"}


def sign(agent_id: str, payload: Any, private_key: Ed25519PrivateKey, *, now: int | None = None) -> dict[str, Any]:
    """Wrap *payload* in an envelope and sign its canonical JSON."""
    envelope = {"agent_id": agent_id, "timestamp": int(time.time()) if now is None else now, "payload": payload}
    return {
        "message": envelope,
        "signature": private_key.sign(canonical(envelope)).hex(),
        "public_key": public_key_hex(private_key),
    }


def verify(signed: dict[str, Any], public_key_hex_value: str) -> bool:
    """True only if *signed* carries a valid signature by the given public key.

    The trusted key is always the caller's; a ``public_key`` field inside the
    document is informational and never used for verification.
    """
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex_value.strip()))
        signature = bytes.fromhex(signed["signature"])
        message = signed["message"]
    except (KeyError, TypeError, ValueError):
        return False
    if not isinstance(message, dict):
        return False
    try:
        public_key.verify(signature, canonical(message))
    except InvalidSignature:
        return False
    return True
