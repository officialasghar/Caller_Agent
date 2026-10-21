"""Capture stable SIP participant metadata for a call."""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

from livekit import rtc

from models.base_agent import CallData


def participant_fields(data: CallData, participant: Any) -> None:
    """Copy SIP attributes while the participant is available in the room."""
    attrs = getattr(participant, "attributes", {}) or {}
    is_sip = any(
        key in attrs for key in ("sip.phoneNumber", "sip.callID", "sip.trunkID")
    )
    kind = getattr(participant, "kind", None)
    if kind is not None:
        with suppress(TypeError, ValueError):
            is_sip = is_sip or rtc.ParticipantKind.Name(kind).endswith("SIP")
    raw_number = attrs.get("sip.phoneNumber")
    if raw_number and not data.caller_number:
        raw_number = str(raw_number).strip()
        digits = re.sub(r"\D", "", raw_number)
        data.caller_number_original = raw_number
        data.caller_number = (
            f"+{digits}" if raw_number.startswith("+") and digits else raw_number
        )
    identity = getattr(participant, "identity", None)
    if identity and is_sip:
        if not data.sip_identity:
            data.sip_identity = str(identity)
        if (
            not raw_number
            and not data.caller_number
            and re.fullmatch(r"\+?[0-9][0-9(). -]{5,}[0-9]", str(identity))
        ):
            data.caller_number_original = str(identity)
            digits = re.sub(r"\D", "", str(identity))
            data.caller_number = (
                f"+{digits}" if str(identity).startswith("+") else str(identity)
            )
    if attrs.get("sip.callID") and not data.sip_call_id:
        data.sip_call_id = str(attrs["sip.callID"])
    if attrs.get("sip.trunkID") and not data.sip_trunk_id:
        data.sip_trunk_id = str(attrs["sip.trunkID"])
