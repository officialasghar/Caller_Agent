import os
import uuid
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from livekit import api

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env.local")


async def token(request: web.Request):
    room = f"web-{uuid.uuid4().hex[:8]}"
    jwt = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(f"user-{uuid.uuid4().hex[:6]}")
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .with_room_config(
            api.RoomConfiguration(
                agents=[api.RoomAgentDispatch(agent_name="my-agent")],
                empty_timeout=10,
                departure_timeout=10,
            )
        )
        .to_jwt()
    )
    return web.json_response({"token": jwt, "url": os.environ["LIVEKIT_URL"]})


async def index(request: web.Request):
    return web.FileResponse(ROOT / "index.html")


app = web.Application()
app.add_routes(
    [
        web.get("/", index),
        web.get("/token", token),
    ]
)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=8080)
