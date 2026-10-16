import os
import logging

from livekit import api, rtc
from livekit.agents import RunContext, function_tool, get_job_context

logger = logging.getLogger("doctor-transfer")


@function_tool()
async def transfer_to_doctor(ctx: RunContext) -> str:
    """Connect the phone caller to the doctor when medical assistance is needed. and just say "Please hold while I connect you to the doctor." before transferring the call. """

    doctor_phone = os.getenv("DOCTOR_PHONE")
    trunk_id = os.getenv("OUTBOUND_TRUNK_ID")

    if not doctor_phone or not trunk_id:
        logger.error("DOCTOR_PHONE or OUTBOUND_TRUNK_ID missing")
        return "Doctor transfer is not configured."

    job_ctx = get_job_context()

    sip_caller = next(
        (
            p
            for p in job_ctx.room.remote_participants.values()
            if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        ),
        None,
    )

    if sip_caller is None:
        return "Doctor transfer is only available for phone calls."

    try:

        # Dial the doctor into the same room using your Twilio trunk.
        logger.info(
            "Dialing doctor %s in room %s",
            doctor_phone,
            job_ctx.room.name,
        )

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

            job_ctx.shutdown(reason="transferred to doctor")
            return "Doctor connected."

        except Exception:
            logger.exception("Failed to connect the doctor")
            return (
                "The doctor did not answer. Tell the caller you could not connect "
                "them and offer to keep helping."
            )

    except Exception:
        logger.exception("Doctor transfer failed")
        return (
            "The doctor transfer failed. Tell the caller you could not connect "
            "them and offer to keep helping."
        )