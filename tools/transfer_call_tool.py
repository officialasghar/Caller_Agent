import logging
import os

from livekit import api, rtc
from livekit.agents import RunContext, function_tool, get_job_context

from models.base_agent import get_call

logger = logging.getLogger("doctor-transfer")


@function_tool()
async def transfer_to_doctor(ctx: RunContext) -> str:
    """Connect the phone caller to the doctor when medical assistance is needed. and just say "Please hold while I connect you to the doctor." before transferring the call."""

    doctor_phone = os.getenv("DOCTOR_PHONE")
    trunk_id = os.getenv("OUTBOUND_TRUNK_ID")

    job_ctx = get_job_context()
    call_data = get_call(job_ctx.room.name)
    if call_data:
        call_data.transfer_requested = True
        call_data.transfer_destination = doctor_phone or None

    if not doctor_phone or not trunk_id:
        if call_data:
            call_data.transfer_result = "failed"
            call_data.transfer_failure_reason = "Transfer configuration unavailable"
        logger.error("DOCTOR_PHONE or OUTBOUND_TRUNK_ID missing")
        return "Doctor transfer is not configured."

    sip_caller = next(
        (
            p
            for p in job_ctx.room.remote_participants.values()
            if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        ),
        None,
    )

    if sip_caller is None:
        if call_data:
            call_data.transfer_result = "unavailable"
            call_data.transfer_failure_reason = "No SIP caller participant found"
        return "Doctor transfer is only available for phone calls."

    try:
        if call_data:
            call_data.transfer_initiated = True

        # Dial the doctor into the same room using your Twilio trunk.
        logger.info("Dialing doctor destination in room %s", job_ctx.room.name)

        try:
            await job_ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=trunk_id,
                    sip_call_to=doctor_phone,
                    room_name=job_ctx.room.name,
                    participant_identity="doctor",
                    participant_name="Doctor",
                    wait_until_answered=True,
                )
            )

            logger.info("Doctor answered in room %s", job_ctx.room.name)

            if call_data:
                call_data.transfer_result = "destination_answered_and_joined_room"

            job_ctx.shutdown(reason="transferred to doctor")
            return "Doctor connected."

        except Exception:
            if call_data:
                call_data.transfer_result = "failed"
                call_data.transfer_failure_reason = "SIP participant creation failed"
            logger.exception("Failed to connect the doctor")
            return (
                "The doctor did not answer. Tell the caller you could not connect "
                "them and offer to keep helping."
            )

    except Exception:
        if call_data:
            call_data.transfer_result = "failed"
            call_data.transfer_failure_reason = "Transfer operation failed"
        logger.exception("Doctor transfer failed")
        return (
            "The doctor transfer failed. Tell the caller you could not connect "
            "them and offer to keep helping."
        )
