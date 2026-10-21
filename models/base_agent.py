"""Per-call metadata and transcript state with strict JSON-safe serialization."""

from __future__ import annotations

import asyncio
import inspect
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def resolve_room_sid(room: Any, timeout_seconds: float = 5) -> str | None:
    """Resolve LiveKit RTC Room.sid, which is an async property in the installed SDK."""
    try:
        return await asyncio.wait_for(room.sid, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return None


def json_safe(value: Any) -> Any:
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError("Awaitable values cannot be serialized in a call report")
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return json_safe(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump(mode="json"))
    raise TypeError(f"Unsupported call report value: {type(value).__name__}")


@dataclass
class CallData:
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    room_name: str | None = None
    room_sid: str | None = None
    job_id: str | None = None
    agent_name: str | None = None
    caller_number: str | None = None
    caller_number_original: str | None = None
    sip_identity: str | None = None
    sip_call_id: str | None = None
    sip_trunk_id: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    status: str = "in_progress"
    disconnect_reason: str | None = None
    transfer_requested: bool = False
    transfer_initiated: bool = False
    transfer_destination: str | None = None
    transfer_result: str | None = None
    transfer_failure_reason: str | None = None
    report_generation_status: str = "pending"
    transcript: list[dict[str, Any]] = field(default_factory=list)
    _item_ids: dict[str, int] = field(default_factory=dict, repr=False)

    def update_available(self, **values: Any) -> None:
        for key, value in values.items():
            if value is not None and value != "":
                if not hasattr(self, key) or key.startswith("_"):
                    raise AttributeError(f"Unknown call data field: {key}")
                setattr(self, key, value)

    def add_transcript_item(
        self, *, item_id: str, speaker: str, text: str, timestamp: datetime | None
    ) -> None:
        if speaker not in {"Caller", "Assistant"} or not text.strip():
            return
        item = {
            "turn_id": item_id,
            "speaker": speaker,
            "text": text.strip(),
            "timestamp": timestamp,
        }
        index = self._item_ids.get(item_id)
        if index is None:
            self._item_ids[item_id] = len(self.transcript)
            self.transcript.append(item)
        else:
            self.transcript[index] = item

    def finalize(self, reason: str | None = None) -> None:
        if self.ended_at is not None:
            return
        self.ended_at = utc_now()
        self.duration_seconds = max(
            0.0, (self.ended_at - self.started_at).total_seconds()
        )
        self.disconnect_reason = reason or self.disconnect_reason
        self.status = (
            "ended_during_transfer"
            if reason and "transfer" in reason.lower()
            else "ended"
        )

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {key: value for key, value in vars(self).items() if not key.startswith("_")}
        )


_by_room: dict[str, CallData] = {}


def register_call(data: CallData) -> None:
    if data.room_name:
        _by_room[data.room_name] = data


def get_call(room_name: str) -> CallData | None:
    return _by_room.get(room_name)


def unregister_call(room_name: str) -> None:
    _by_room.pop(room_name, None)


def safe_filename(call_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", call_id).strip("._")
    return name or str(uuid.uuid4())
