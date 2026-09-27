"""Inbound message event types shared by every gateway platform adapter.

A leaf module: adapters, helpers and the runner import it, so it must not import from
gateway.platforms.*.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from gateway.session import SessionSource


class MessageType(Enum):
    """Types of incoming messages."""
    TEXT = "text"
    LOCATION = "location"
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    DOCUMENT = "document"
    STICKER = "sticker"
    COMMAND = "command"  # /command style


class ProcessingOutcome(Enum):
    """Result classification for message-processing lifecycle hooks."""
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


def _coerce_duration_ms(duration_ms: Any, duration_seconds: Any) -> Optional[int]:
    """Whole positive milliseconds from whichever unit the platform reports; None when unusable.

    ``bool`` is rejected explicitly — it is an ``int`` subclass, so a stray ``voice=True`` flag
    would otherwise normalize to a 1-second clip.
    """
    for value, scale in ((duration_ms, 1), (duration_seconds, 1000)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        if (ms := int(round(value * scale))) > 0:
            return ms
    return None


def _normalize_source_format(value: Any) -> Optional[str]:
    """Container/codec as a bare lowercase token: ``"audio/ogg"``, ``".OGG"``, ``"audio/x-m4a;
    codecs=opus"`` all become ``"ogg"`` / ``"m4a"``. None when unusable."""
    if not isinstance(value, str):
        return None
    text = value.split(";", 1)[0].strip().lower()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.lstrip(".").removeprefix("x-") or None


@dataclass(frozen=True)
class VoiceMeta:
    """Normalized inbound voice-message metadata — one shape for every adapter.

    Platforms report the same few facts under different names and units: Telegram's
    ``voice.duration`` is whole seconds, Matrix's ``info.duration`` is milliseconds, Discord's
    ``attachment.duration`` is fractional seconds, and DingTalk's ``recognition`` / Weixin's
    ``voice_item.text`` are the platform's OWN speech-to-text output. Adapters normalize once
    through :meth:`build`; consumers read these fields and never branch on the platform.

    ``text_hint`` is a hint, never a transcript. Native ASR is frequently wrong for audio outside
    the platform's primary language (#27300: Russian audio came back as English gibberish), so
    consumers must prefer Hermes's configured STT and fall back to the hint only when STT produced
    no words. Anything beyond this shared subset stays in ``MessageEvent.metadata``.
    """

    text_hint: Optional[str] = None
    duration_ms: Optional[int] = None
    source_format: Optional[str] = None

    @classmethod
    def build(cls, *, text_hint: Any = None, duration_ms: Any = None,
              duration_seconds: Any = None, source_format: Any = None) -> "Optional[VoiceMeta]":
        """Coerce raw platform fields into a ``VoiceMeta``, or None when nothing usable survived.

        Returning None (rather than an all-empty instance) lets an adapter assign unconditionally —
        ``event.voice = VoiceMeta.build(...)`` — instead of each one re-deriving "is any of this
        worth carrying?".
        """
        hint = text_hint.strip() if isinstance(text_hint, str) else None
        ms = _coerce_duration_ms(duration_ms, duration_seconds)
        fmt = _normalize_source_format(source_format)
        return cls(text_hint=hint or None, duration_ms=ms, source_format=fmt) if (hint or ms or fmt) else None


@dataclass
class MessageEvent:
    """Incoming message from a platform — the normalized shape all adapters produce."""
    text: str
    message_type: MessageType = MessageType.TEXT
    # Author, mirrored from ``source`` for per-message prompt builders; None for non-IM sources.
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    # None only in isolated unit tests; production always sets it. Typing it Optional
    # exposes ~60 unguarded ``.source.<attr>`` reads, so that is a separate change.
    source: SessionSource = None
    raw_message: Any = None
    message_id: Optional[str] = None
    # Delivery-ledger identity for the final send, when it differs from ``message_id``. A queued
    # (/queue) chain answers the LAST message of the chain, so its final send has to be ledgered
    # under that message's id. Keyed on the opening event's id instead, two chained turns carrying
    # the same text collide on one obligation id and the earlier turn's row is overwritten (a
    # refused first reply then reads as delivered). Reply routing is unaffected: the reply anchor
    # still comes from this event.
    ledger_message_id: Optional[str] = None
    # Reply anchor for the final send when the answer is to a DIFFERENT message than the one that
    # opened the turn: a successful busy redirect turns the running turn onto the redirecting
    # message, so its reply must quote that message (#115001). ``_reply_anchor_for_event``
    # honours this over ``message_id``; None = derive from the event as usual.
    reply_anchor_override: Optional[str] = None
    # Platform update id (Telegram ``update_id``): ``/restart`` records it so the new gateway
    # advances past it even if PTB's shutdown ACK times out.
    platform_update_id: Optional[int] = None
    # Media attachments: local file paths (for vision tool access)
    media_urls: List[str] = field(default_factory=list)
    media_types: List[str] = field(default_factory=list)
    # Per-attachment text-inlining contract; None = legacy "text/* already inlined into ``text``".
    media_text_inlined: List[Optional[bool]] = field(default_factory=list)
    reply_to_message_id: Optional[str] = None
    reply_to_text: Optional[str] = None  # Text of the replied-to message (for context injection)
    reply_to_author_id: Optional[str] = None
    reply_to_author_name: Optional[str] = None
    reply_to_is_own_message: bool = False  # True when the user replied to this bot/assistant's message
    # Structured interactive-prompt reply (relay only): {prompt_id, option_id, label?,
    # prompt_message_id?}; routed to the approval/slash-confirm/clarify resolvers BEFORE dispatch.
    prompt_response: Optional[Dict[str, Any]] = None
    # Auto-loaded skill(s) for topic/channel bindings; a single name or ordered list.
    auto_skill: Optional[str | list[str]] = None
    # Per-channel ephemeral system prompt; applied at API call time, never persisted to transcript.
    channel_prompt: Optional[str] = None
    # History-backfilled channel context (missed under require_mention); kept out of ``text`` so
    # run.py's sender-prefix logic sees only the trigger message.
    channel_context: Optional[str] = None
    # Set for synthetic events (e.g. background-process notifications) that must bypass user authorization.
    internal: bool = False
    # Free-form per-event metadata (e.g. ``whatsapp_from_owner=True``); plugins must ``.get()``.
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    # May this event resolve gateway commands / control prompts? Proactive plugin events set False
    # so untrusted payload text stays conversational. New fields append after it (positional compat).
    allow_gateway_control: bool = True
    # Was this message addressed to this bot? False lets a bare silence marker stand (the adapter
    # knows the message was meant for someone else); None means unknown and keeps the visible
    # fallback, like True.
    reply_expected: Optional[bool] = None
    # Normalized inbound voice-message metadata; None when the platform exposes none. The shared
    # subset consumers may read without knowing the platform — adapter-specific extras stay in
    # ``metadata``. Describes ONE clip, so consumers must ignore it once several have merged into
    # one event (``merge_pending_message_event`` extends ``media_urls``).
    voice: Optional[VoiceMeta] = None

    # Process-local admission receipt, never routing metadata or execution acknowledgement.
    _gateway_accepted: bool = field(default=False, init=False, repr=False, compare=False)
    # Run-owned final presentation snapshot; never deserialized from ingress metadata.
    _notification_reply_muted: Optional[bool] = field(default=None, init=False, repr=False, compare=False)

    def absorb_reply_expected(self, other: "MessageEvent") -> None:
        """One turn now answers *other* too: an addressed message wins, then an unknown one."""
        if self.reply_expected is not True and other.reply_expected is not False:
            self.reply_expected = other.reply_expected

    def is_command(self) -> bool:
        """Check if this is a command message (e.g., /new, /reset)."""
        return self.allow_gateway_control and (self.text or "").lstrip().startswith("/")

    def get_command(self) -> Optional[str]:
        """Extract command name if this is a command message."""
        if not self.is_command():
            return None
        raw = (self.text or "").lstrip().split(maxsplit=1)[0][1:].lower().split("@", 1)[0]
        # Reject file paths: valid command names never contain /
        return None if "/" in raw else raw

    def get_command_args(self) -> str:
        """Get the arguments after a command."""
        if not self.is_command():
            return self.text
        parts = (self.text or "").lstrip().split(maxsplit=1)
        args = parts[1] if len(parts) > 1 else ""
        # iOS auto-corrects -- to — (em dash) and - to – (en dash)
        return args.replace("\u2014\u2014", "--").replace("\u2014", "--").replace("\u2013", "-")
