# feat(gateway): normalized inbound voice-message metadata (`MessageEvent.voice`)

Closes #11687.

Standardizes the small contract the issue asked for — `voice_text_hint`, `voice_duration_ms`,
`voice_source_format` — as one normalized object on `MessageEvent`, and wires the two consumers that
make it worth carrying. No emotion classification, no profile persistence, no JSONL ledgers, no
analysis hook: the issue's own out-of-scope list is respected exactly.

## The gap, concretely

The issue describes the symptom in the abstract; the tree has it in three places.

**Weixin drops the data.** `voice_item.text` is Tencent's own ASR. #27300 established that it is
garbage for non-Chinese audio (a Russian voice note came back as English phonemes), so
`_extract_text()` returns `""` whenever raw audio exists and the central STT pipeline owns the body.
Correct — but the hint is then *discarded*, including in the one case where it is the best answer
left: Hermes's STT failing, or `stt.enabled: false`. The user gets
`[voice message could not be transcribed automatically]` while the platform's own words sat in the
payload.

**DingTalk special-cases itself to protect against exactly that** (`plugins/platforms/dingtalk/inbound.py`):

```python
elif msg_type_str == "audio":
    # Voice message: recognition text is already in the text. Do NOT add media_urls, or
    # run.py's transcription enrichment overwrites it with a failed STT attempt.
```

An adapter opting out of the shared media path because the shared path would clobber its metadata is
the "downstream logic has to special-case platforms" failure the issue names.

**Telegram and Discord drop it silently.** `msg.voice.duration` and `attachment.duration` are right
there in the update; the gateway then spawns an `ffprobe` subprocess per clip to learn the same
number (`_probe_audio_duration`).

## The contract

`gateway/platforms/event.py` (leaf module, no new imports beyond `math`):

```python
@dataclass(frozen=True)
class VoiceMeta:
    text_hint: Optional[str] = None       # the PLATFORM's ASR — a hint, never a transcript
    duration_ms: Optional[int] = None
    source_format: Optional[str] = None   # bare lowercase token: "ogg", "silk", "m4a"

    @classmethod
    def build(cls, *, text_hint=None, duration_ms=None, duration_seconds=None, source_format=None): ...
```

```python
# on MessageEvent, appended last (the file's positional-compat rule)
voice: Optional[VoiceMeta] = None
```

`build()` is where the per-platform coercion lives, so no adapter repeats it:

- **either duration unit** — Telegram reports whole seconds, Matrix milliseconds, Discord fractional
  seconds; `duration_seconds=4.5` and `duration_ms=4500` produce the same object.
- **any format spelling** — `"audio/ogg"`, `".OGG"`, `"audio/x-m4a; codecs=opus"` all reduce to a
  bare token.
- **`None` when nothing usable survived**, so an adapter writes `event.voice = VoiceMeta.build(...)`
  with no `if` and no all-empty instance for consumers to re-check field by field.
- **`bool` rejected explicitly** — it is an `int` subclass, so a stray `voice=True` flag would
  otherwise normalize to a 1-second clip.

Anything outside this subset stays in the existing free-form `MessageEvent.metadata`. That split is
the answer to the issue's "stable place for adapter-specific / normalized metadata": `metadata` was
already the adapter-specific place; `voice` is the normalized one.

## Two real consumers (not a hook waiting for one)

Both in `gateway/run_inbound.py`, both on the live inbound path:

1. **STT fallback.** When configured STT fails, is unavailable, or returns no words,
   `_transcribe_one_clip` now emits the platform's hint instead of the "could not be transcribed"
   marker. It is labelled `[Voice message transcribed by the platform, not by Hermes — may be
   inaccurate]` so the agent can hedge, and it never joins `successful_transcripts` — that list is
   echoed to the chat as `🎙️ "…"`, and the user already saw this text there.
2. **Duration without the subprocess.** With `stt.enabled: false` the gateway annotates the clip
   length; when the adapter already reported it, `ffprobe` is not spawned.

**The hint never outranks real STT.** That is the invariant #27300 bought, and it is what makes the
field safe to populate on a platform whose ASR is unreliable.

## Two invariants the tests pin

- **A successful STT transcript always wins.** The hint is reachable only after STT has produced no
  words.
- **`voice` describes one clip.** `merge_pending_message_event` appends a second voice note onto the
  same event while `voice` still describes the first, so a multi-clip event ignores it and falls back
  to per-file probing. Without that guard one clip's transcript would be attributed to another's
  audio.

## Adapters populated

| Adapter | Carries | Why this one |
|---|---|---|
| `gateway/platforms/weixin.py` | `text_hint` (+ `source_format="silk"`) | the hint it currently discards; body still owned by central STT |
| `plugins/platforms/telegram/adapter.py` | `duration_ms`, `source_format` | `msg.voice.duration` / `mime_type`, voice notes only (a `message.audio` file gets nothing — #24870 keeps it off STT) |
| `plugins/platforms/discord/adapter.py` | `duration_ms`, `source_format` | native voice notes only, via the existing `_is_discord_voice_message_attachment` probe |

Each one feeds a live consumer, so nothing here is a field set and never read. `ADDING_A_PLATFORM.md`
gets the contract plus the three rules, so the next adapter normalizes instead of inventing.

## Validation

`scripts/run_tests.sh tests/gateway/ tests/plugins/platforms/` — green. `ruff check` clean on all
changed files.

New: `tests/gateway/test_inbound_voice_metadata.py`, 12 contracts —
normalization (both units agree, every format spelling reduces, `None` when empty, `bool` rejected);
consumers (real transcript wins; failed STT falls back without echoing; empty STT result falls back;
merged multi-clip event ignores the hint; reported duration skips the probe; missing duration still
probes); adapters (Weixin carries the hint while still returning `""` as the body, Discord
normalizes a native note and ignores a plain audio attachment, Telegram carries a voice note's length
but not an audio file's).

Because the type is new, these fail on base as an `ImportError` rather than as behaviour diffs —
there is no way to express "the hint is used" against a tree with no hint. The two contracts that
*do* constrain existing behaviour (a real transcript is never displaced; a merged event ignores the
hint) are the ones a reviewer should read first.

Updated: `tests/gateway/test_busy_session_ack.py::test_steer_mode_transcribes_voice_before_injection`
asserts the exact call into `_enrich_message_with_transcription`, which now takes `voice=`. The
assertion gained `voice=None`; its subject (steer mode transcribes before injecting) is unchanged.

`tests/gateway/test_update_streaming.py::test_gateway_flag_enables_gateway_prompt_for_stash` fails on
this machine — it trips the real-`~/.hermes` I/O guard. Confirmed failing identically on unmodified
`origin/main`; unrelated to this change.

### A pre-existing spin-loop this PR kept tripping (second commit, separable)

`test_kanban_wake_acceptance.py` was SIGKILLed at the runner's 300s per-file cap on three
consecutive runs of this branch, turning a 130s suite into 349s. A faulthandler dump of the live
process (state `Rsl`, 59s CPU in 71s wall — spinning, not deadlocked) pinned it to the file's own
helper:

```python
async def drain(adapter):
    while adapter._background_tasks:
        await asyncio.gather(*list(adapter._background_tasks))
```

`_background_tasks` is emptied by `task.add_done_callback(self._background_tasks.discard)`
(`platforms/base.py:3890`), which the loop runs via `call_soon` — so the set is still non-empty the
instant `gather` returns on already-done tasks, and with no yield in the body the `while` re-gathers
finished tasks forever without letting the discards run.

It is **not caused by this change**, proven three ways:

| Experiment | Files | Result | `kanban_wake_acceptance` |
|---|---|---|---|
| this branch's source, new test file held aside | 1012 | 9087 ✓ / 1 ✗, 128.3s | ✓ 5.4s |
| clean `origin/main`, same targets | 1012 | 9087 ✓ / 1 ✗, 128.0s | ✓ 5.7s |
| clean `origin/main` + one dummy test file | 1013 | 9088 ✓ / 1 ✗, 114.2s | ✓ 4.7s |
| this branch as committed | 1013 | 9095 ✓ / 1 ✗, 349s | ✗ SIGKILL ×3 |

Row 1 is decisive: with the feature code and no new test file the run is identical to base, so the
source changes are clean. Row 3 rules out file count. The captured stack touches nothing in this
diff. Adding any file perturbs `-j32` timing enough to tip the latent spin.

The second commit adds the missing `await asyncio.sleep(0)` plus a 10s deadline (a stuck task now
fails as a named assertion instead of hanging until the killer), in **both** copies — the helper is
duplicated verbatim in `evals/heartbeat_idle_wire.py`, whose four call sites have the same defect.
With it: 129.9s, kanban green in 5.9s, and the eval still runs clean end to end. Kept as its own
commit since it is unrelated to #11687 and can be taken or dropped independently.

## Deliberately not in this PR

- **The relay wire (`gateway/relay/ws_transport.py`, §3 of the connector contract).** Adding a
  `voice` object to the inbound frame is additive and easy, but it changes an external, versioned
  contract with no connector producing the field yet. Follow-up.
- **Rerouting DingTalk.** Now that the hint survives a failed STT, DingTalk can stop opting out of
  `media_urls` and hand over audio *and* `recognition`. That flips a platform's media routing and
  adds a download + STT call per voice note, which is a behaviour change to argue on its own, not a
  rider on the contract that enables it. Same for WeCom (`_content_of(body, "voice")`), which never
  downloads the audio at all.
- **The optional voice-analysis hook.** The issue explicitly asks to standardize the metadata first
  and decide on any general analysis hook afterwards. Nothing here presumes the answer.
