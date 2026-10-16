import asyncio
import logging

from livekit import api
from livekit.agents import RunContext, function_tool, get_job_context

logger = logging.getLogger("agent")
_ending: set[str] = set()
_tasks: set[asyncio.Task] = set()


async def _hang_up(session, job_ctx):
    try:
        # wait for the model's goodbye to start (max 4s)
        for _ in range(40):
            if session.agent_state == "speaking":
                break
            await asyncio.sleep(0.1)
        # wait until it has been quiet for 1.2s continuously (max ~20s)
        quiet = 0
        for _ in range(200):
            quiet = 0 if session.agent_state == "speaking" else quiet + 1
            if quiet >= 12:
                break
            await asyncio.sleep(0.1)
        await job_ctx.api.room.delete_room(api.DeleteRoomRequest(room=job_ctx.room.name))
    except Exception:
        logger.exception("hang up failed")


@function_tool
async def end_call(context: RunContext):
    """End the call when the caller says goodbye, bye, no thank you, that's all,
    or asks to end the call. Do not use it if the caller is still asking something."""
    job_ctx = get_job_context()
    room = job_ctx.room.name
    if room in _ending:
        return "The call is already ending. Say nothing."
    _ending.add(room)
    logger.info("Ending the call")

    t = asyncio.create_task(_hang_up(context.session, job_ctx))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    # The model speaks the single goodbye after this result; the call hangs up when it finishes.
    return "The call is ending. Say one short goodbye sentence now, nothing else."