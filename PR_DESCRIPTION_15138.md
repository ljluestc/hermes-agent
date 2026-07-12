# fix(image_gen): ground named-entity prompts in Codex image instructions

Fixes #15138

## Summary

The `openai-codex` image-gen plugin was using a single bare instruction to the
intermediate chat model (`gpt-5.5`):

> "You are an assistant that must fulfill image generation requests by using the
> image_generation tool when provided."

With that minimal system prompt, `gpt-5.5` would rewrite or freely interpret
the user's prompt before calling `image_generation`, especially for non-English
named-character requests. The result was an **original character** instead of
the requested one (e.g. asking for 灰原哀 / Ai Haibara from Detective Conan
produced an invented "白川灯里 / Lucid Lab" character sheet).

The same prompt works in the ChatGPT web UI because that UI applies its own
richer system context and entity-grounding layer before the image_generation
call. This PR brings the Hermes path to parity by expanding
`_CODEX_INSTRUCTIONS` with four explicit rules.

## Root Cause

`_build_responses_payload` passes the user's raw prompt as a `user` message to
the Codex Responses API. The intermediary `gpt-5.5` model is then free to
compose its own prompt for the `image_generation` tool call. With no
entity-grounding guidance it treats well-known named characters (especially
non-ASCII names) as stylistic inspiration rather than identity anchors,
generating an original character instead.

## Changes

### `plugins/image_gen/openai-codex/__init__.py`

Replaced `_CODEX_INSTRUCTIONS` with a four-rule system prompt:

**Before:**
```python
_CODEX_INSTRUCTIONS = (
    "You are an assistant that must fulfill image generation requests by "
    "using the image_generation tool when provided."
)
```

**After:**
```python
_CODEX_INSTRUCTIONS = (
    "You are an assistant that fulfills image generation requests by calling "
    "the image_generation tool. Follow these rules strictly:\n"
    "1. Call the image_generation tool exactly once per request.\n"
    "2. Preserve all named characters, proper nouns, and fictional entities "
    "from the user's prompt verbatim — do NOT substitute, rename, or invent "
    "new characters. If the user asks for a named character (e.g. from an "
    "anime, game, or other IP), use that exact character identity in the tool "
    "call.\n"
    "3. If the prompt is in a non-English language, translate and expand it "
    "into a detailed English image_generation prompt while preserving every "
    "named entity from the original language.\n"
    "4. Do not add fictional organizations, invented names, or original "
    "characters that were not in the user's request."
)
```

The four rules address the specific failure modes observed in #15138:

| Rule | Problem it prevents |
|------|---------------------|
| 1 — call tool exactly once | Occasional multi-call or no-call loops |
| 2 — preserve named entities verbatim | Drift from known IP characters to original creations |
| 3 — translate + expand non-English prompts | Named entities stripped or mistranslated before tool call |
| 4 — no invented details | Fabricated organizations, alternate names added by the model |

## Reproduction / Verification

**Before fix** — both prompts produce original characters:
```
生成一张名侦探柯南里灰原哀的角色设定图
灰原哀的角色设定图
```

**After fix** — `gpt-5.5` is instructed to carry 灰原哀 (Ai Haibara, Detective
Conan) as the grounded identity into the `image_generation` tool call instead
of substituting an invented character.

No config changes are required. Existing `image_gen.openai-codex.model` and
`image_gen.model` config keys continue to work as before.

## Related

- #14317 — feat: add openai-codex image-gen plugin (introduced the bare instruction)
- #14819 — feat: add per-call Codex output controls (adds `gpt-image-2-auto`; orthogonal to this fix but noted in the issue)

## Files Changed

| File | Change |
|------|--------|
| `plugins/image_gen/openai-codex/__init__.py` | Expand `_CODEX_INSTRUCTIONS` with entity-grounding, non-English translation, and no-invention rules |

---

Branch: `private/issue-15138-codex-entity-grounding`
Base: `main`
