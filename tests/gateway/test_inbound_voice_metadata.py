"""Behavior contracts for the normalized inbound voice-message metadata (#11687).

``MessageEvent.voice`` is the one shape every adapter produces for a voice note: a platform-side
transcript hint, a clip duration and a source format. The contracts here pin the two things that
make it safe to consume without knowing the platform — a hint NEVER outranks real STT (#27300), and
per-message metadata is dropped once several clips have merged into one event.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.event import MessageEvent, MessageType, VoiceMeta
from gateway.run import GatewayRunner
from gateway.session import SessionSource

_HINT = "prevedeno na platformi"


def _async_return(value):
    """A zero-arg awaitable stub: ``await obj.method()`` yields ``value``."""
    async def _call(*_args, **_kwargs):
        return value
    return _call


def _make_runner(stt_enabled: bool = True) -> GatewayRunner:
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(stt_enabled=stt_enabled)
    runner.adapters = {}
    runner._model = "test-model"
    runner._base_url = ""
    runner._has_setup_skill = lambda: False
    return runner


def _voice_event(*paths: str, voice: VoiceMeta | None = None) -> MessageEvent:
    return MessageEvent(
        text="", message_type=MessageType.VOICE,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="1", chat_type="dm"),
        media_urls=list(paths), media_types=["audio/ogg"] * len(paths), voice=voice,
    )


# ── the contract itself ──────────────────────────────────────────────────────

def test_build_normalizes_either_duration_unit_to_one_shape():
    """Seconds and milliseconds are the same fact; consumers must not have to ask which."""
    from_seconds = VoiceMeta.build(duration_seconds=4.5)
    from_ms = VoiceMeta.build(duration_ms=4500)

    assert from_seconds == from_ms
    assert from_seconds.duration_ms == 4500


def test_build_reduces_any_format_spelling_to_a_bare_token():
    assert {VoiceMeta.build(source_format=v).source_format
            for v in ("audio/ogg", ".OGG", "ogg", "audio/ogg; codecs=opus")} == {"ogg"}
    assert VoiceMeta.build(source_format="audio/x-m4a").source_format == "m4a"


def test_build_returns_none_when_nothing_usable_survived():
    """Adapters assign unconditionally, so "no metadata" has to be representable as None rather than
    an all-empty instance every consumer then has to re-check field by field."""
    assert VoiceMeta.build() is None
    assert VoiceMeta.build(text_hint="   ", duration_ms=0, source_format="") is None
    # bool is an int subclass: a stray ``voice=True`` flag must not become a 1-second clip.
    assert VoiceMeta.build(duration_seconds=True) is None


# ── consumer: the hint is a fallback, never a preference ─────────────────────

@pytest.mark.asyncio
async def test_real_transcript_wins_over_the_platform_hint():
    """#27300: platform ASR is wrong for audio outside the platform's primary language, so a
    successful STT run must never be displaced by the hint that came with the message."""
    runner = _make_runner()
    event = _voice_event("/tmp/voice.ogg", voice=VoiceMeta.build(text_hint=_HINT))

    with patch("tools.transcription_tools.transcribe_audio",
               return_value={"success": True, "transcript": "hello world", "provider": "whisper"}):
        text, transcripts = await runner._enrich_message_with_transcription(
            "", ["/tmp/voice.ogg"], voice=event.voice)

    assert "hello world" in text
    assert _HINT not in text
    assert transcripts == ["hello world"]


@pytest.mark.asyncio
async def test_failed_stt_falls_back_to_the_platform_hint_without_echoing_it():
    """A failed STT used to leave only "could not be transcribed" even when the platform had already
    sent its own words. The hint is not a transcript, so it must not join ``successful_transcripts``
    — that list is echoed back to the chat, and the user already saw this text there."""
    runner = _make_runner()

    with patch("tools.transcription_tools.transcribe_audio",
               return_value={"success": False, "error": "no provider"}), \
         patch("tools.transcription_tools.transcribe_audio_local_fallback",
               return_value={"success": False, "error": "no local stt"}):
        text, transcripts = await runner._enrich_message_with_transcription(
            "", ["/tmp/voice.ogg"], voice=VoiceMeta.build(text_hint=_HINT))

    assert _HINT in text
    assert "could not be transcribed" not in text
    # Labelled as platform-provided so the agent can hedge instead of quoting it as verbatim.
    assert "transcribed by the platform" in text
    assert transcripts == []


@pytest.mark.asyncio
async def test_empty_stt_result_falls_back_to_the_platform_hint():
    """STT can return success with no words (silence, cut-off). The "ask the user to resend"
    sentinel is the right answer only when nothing else knows what was said."""
    runner = _make_runner()

    with patch("tools.transcription_tools.transcribe_audio",
               return_value={"success": True, "transcript": "   ", "provider": "whisper"}):
        text, _ = await runner._enrich_message_with_transcription(
            "", ["/tmp/voice.ogg"], voice=VoiceMeta.build(text_hint=_HINT))

    assert _HINT in text
    assert "resend" not in text


@pytest.mark.asyncio
async def test_hint_is_ignored_once_several_clips_merged_into_one_event():
    """``merge_pending_message_event`` appends a second voice note to the same event, but
    ``voice`` still describes the first. Attributing its words to a burst would put one clip's
    transcript against another's audio."""
    runner = _make_runner()

    with patch("tools.transcription_tools.transcribe_audio",
               return_value={"success": False, "error": "no provider"}), \
         patch("tools.transcription_tools.transcribe_audio_local_fallback",
               return_value={"success": False, "error": "no local stt"}):
        text, _ = await runner._enrich_message_with_transcription(
            "", ["/tmp/a.ogg", "/tmp/b.ogg"], voice=VoiceMeta.build(text_hint=_HINT))

    assert _HINT not in text
    assert text.count("could not be transcribed") == 2


# ── consumer: the reported duration replaces the probe ──────────────────────

@pytest.mark.asyncio
async def test_reported_duration_is_used_instead_of_probing_the_file():
    """With STT off the gateway annotates the clip's length. The platform already measured it, so
    spawning ffprobe to learn the same number is pure latency."""
    runner = _make_runner(stt_enabled=False)

    with patch("gateway.run._probe_audio_duration") as probe:
        text, _ = await runner._enrich_message_with_transcription(
            "", ["/tmp/voice.ogg"], voice=VoiceMeta.build(duration_ms=95_000))

    probe.assert_not_called()
    assert "(duration: 1:35)" in text


@pytest.mark.asyncio
async def test_missing_reported_duration_still_probes_the_file():
    runner = _make_runner(stt_enabled=False)

    with patch("gateway.run._probe_audio_duration", return_value="0:07") as probe:
        text, _ = await runner._enrich_message_with_transcription("", ["/tmp/voice.ogg"])

    probe.assert_called_once()
    assert "(duration: 0:07)" in text


# ── adapters produce the contract ───────────────────────────────────────────

def test_weixin_carries_the_tencent_transcript_instead_of_dropping_it():
    """Weixin returns "" as the body when raw audio exists so the central STT owns the transcript
    (#27300) — which also threw ``voice_item.text`` away. It is now carried as the hint, so the
    fallback exists without ever preempting STT."""
    from gateway.platforms.weixin import ITEM_VOICE, _extract_text, _extract_voice_meta

    item_list = [{"type": ITEM_VOICE, "voice_item": {"text": _HINT, "media": {"full_url": "https://x/y"}}}]

    assert _extract_text(item_list) == ""          # STT still owns the body
    assert _extract_voice_meta(item_list) == VoiceMeta(text_hint=_HINT, source_format="silk")


def test_discord_normalizes_a_native_voice_note_duration():
    """Discord puts ``duration``/``waveform`` on the attachment and has no server-side ASR."""
    from plugins.platforms.discord.adapter import DiscordAdapter

    plain = SimpleNamespace(content_type="audio/ogg", duration=None, waveform=None)
    note = SimpleNamespace(content_type="audio/ogg", duration=3.25, waveform=b"\x80" * 8)

    assert DiscordAdapter._voice_meta_from_attachments([plain]) is None
    assert DiscordAdapter._voice_meta_from_attachments([plain, note]) == VoiceMeta(
        duration_ms=3250, source_format="ogg")


@pytest.mark.asyncio
async def test_telegram_voice_note_carries_its_reported_length_but_an_audio_file_does_not():
    """Telegram states the clip length in the update. A ``message.audio`` file attachment is not a
    voice note (#24870 keeps it off STT), so it gets no voice metadata either."""
    from plugins.platforms.telegram.adapter import TelegramAdapter

    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._telegram_media_size_allowed = lambda source, label: (True, None)
    file_obj = SimpleNamespace(file_path="voice.oga",
                               download_as_bytearray=_async_return(bytearray(b"\x00")))

    async def _cache(_data, *, ext):
        return f"/cache/clip{ext}"

    with patch("plugins.platforms.telegram.adapter.cache_audio_from_bytes_async", _cache):
        events = {}
        for kind, ext, mime in (("voice", ".ogg", "audio/ogg"), ("audio", ".mp3", "audio/mp3")):
            event = _voice_event()
            source = SimpleNamespace(duration=8, mime_type=mime, file_size=1024,
                                     get_file=_async_return(file_obj))
            assert await adapter._cache_inbound_av(
                None, event, source, f"{kind} message", kind, ext, mime) is False
            events[kind] = event

    assert events["voice"].voice == VoiceMeta(duration_ms=8000, source_format="ogg")
    assert events["audio"].voice is None
