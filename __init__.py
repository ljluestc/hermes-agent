"""``hermes agent-identity``: a local Ed25519 identity for the active profile.

No tools, no hooks, no network. The private key is a credential, so it lives in the
profile's ``.env`` as ``AGENT_IDENTITY_SIGNING_KEY`` and is read through the secret
scope; nothing is written under the plugin directory or ``plugin-data/``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import identity

KEY_ENV = "AGENT_IDENTITY_SIGNING_KEY"


def _profile_name() -> str:
    from hermes_cli.profiles import get_active_profile_name

    return get_active_profile_name()


def _private_key():
    from agent.secret_scope import get_secret

    seed = get_secret(KEY_ENV)
    if not seed:
        raise SystemExit(f"No signing key. Run `hermes agent-identity keygen` and save {KEY_ENV} in this profile's .env.")
    try:
        return identity.load_private_key(seed)
    except ValueError as exc:
        raise SystemExit(f"{KEY_ENV} is invalid: {exc}") from exc


def _read_json(source: str) -> Any:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{source}: not valid JSON ({exc})") from exc


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _keygen(_args) -> int:
    seed = identity.generate_seed()
    public = identity.public_key_hex(identity.load_private_key(seed))
    print("Add this line to the profile's .env (or set the plugin's signing key in Desktop → Plugins).")
    print("Anyone with it can sign as this agent; keep it private.\n")
    print(f"{KEY_ENV}={seed}\n")
    print(f"public_key: {public}")
    return 0


def _card(_args) -> int:
    _print(identity.identity_card(_profile_name(), _private_key()))
    return 0


def _sign(args) -> int:
    _print(identity.sign(_profile_name(), _read_json(args.file), _private_key()))
    return 0


def _verify(args) -> int:
    ok = identity.verify(_read_json(args.file), args.public_key)
    print("valid" if ok else "INVALID")
    return 0 if ok else 1


_HANDLERS = {"keygen": _keygen, "card": _card, "sign": _sign, "verify": _verify}


def _command(args) -> int:
    handler = _HANDLERS.get(getattr(args, "agent_identity_command", None))
    if handler is None:
        print("Usage: hermes agent-identity {keygen,card,sign,verify}")
        return 2
    return handler(args)


def _setup_argparse(subparser) -> None:
    subs = subparser.add_subparsers(dest="agent_identity_command")
    subs.add_parser("keygen", help="Generate a new signing key for this profile")
    subs.add_parser("card", help="Print this profile's identity card (agent_id + public key)")
    sign = subs.add_parser("sign", help="Sign a JSON payload as this profile")
    sign.add_argument("file", nargs="?", default="-", help="JSON file to sign (default: stdin)")
    verify = subs.add_parser("verify", help="Verify a signed document against a public key")
    verify.add_argument("file", nargs="?", default="-", help="Signed JSON from `sign` (default: stdin)")
    verify.add_argument("--public-key", required=True, help="Signer's public key (hex), from their identity card")
    subparser.set_defaults(func=_command)


def register(ctx) -> None:
    ctx.register_cli_command(
        name="agent-identity",
        help="Local Ed25519 identity for this profile (keygen, card, sign, verify)",
        setup_fn=_setup_argparse,
        handler_fn=_command,
        description="Give each profile a signing key, publish its identity card, and sign/verify JSON messages locally.",
    )
