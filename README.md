# agent-identity

A standalone [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that gives each profile (or Bot) a local **Ed25519** identity. It adds one CLI command, `hermes agent-identity`, and registers no tools or hooks, so the model's tool schema doesn't change. It never makes network calls.

Background: NousResearch/hermes-agent#20578 proposed the "Works With Agents" identity layer for Hermes. That SDK's identity module verifies signatures on a hosted registry and, without `cryptography`, falls back to a hash that isn't a real signature. This plugin keeps the useful part, which is signed agent messages in the same envelope shape. Keys and verification stay on your machine, using real Ed25519 from `cryptography` (already a Hermes dependency).

## Install

```bash
git clone -b plugin/agent-identity-20578 https://github.com/ljluestc/hermes-agent ~/.hermes/plugins/agent-identity
hermes plugins enable agent-identity
```

## Use

```bash
hermes agent-identity keygen          # prints AGENT_IDENTITY_SIGNING_KEY=… — save it in the profile's .env
hermes agent-identity card            # {"agent_id": "<profile>", "public_key": "…", "algorithm": "ed25519"}
echo '{"type":"heartbeat"}' | hermes agent-identity sign > signed.json
hermes agent-identity verify --public-key <hex> signed.json   # "valid" (exit 0) or "INVALID" (exit 1)
```

Use `hermes -p <bot> agent-identity …` to act as a specific profile or Bot. Each profile reads its own key.

### Signed document

```json
{
  "message": {"agent_id": "researcher", "timestamp": 1790958137, "payload": {"type": "heartbeat"}},
  "signature": "<128 hex chars>",
  "public_key": "<64 hex chars>"
}
```

The signature covers the canonical JSON of `message` (sorted keys, no whitespace). `verify` only trusts the public key you pass on the command line. The `public_key` field in the document is informational and is never used to check the signature.

## Where the key lives

The private key is a credential, so it is stored in the profile's `.env` as `AGENT_IDENTITY_SIGNING_KEY`. You can also set it as the plugin's masked **Signing key** setting in Desktop → Plugins. It is read through Hermes's per-profile secret scope.

- `hermes backup` includes it, because backups include `.env`.
- `hermes profile export` strips it, the same as other credentials.
- Nothing is written to the plugin directory or `plugin-data/`.

## Tests

```bash
python -m pytest tests/
```
