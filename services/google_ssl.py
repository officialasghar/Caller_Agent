"""Google Gen AI HTTP options with SSL setup suitable for an asyncio agent."""

from __future__ import annotations

import os
import ssl
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.genai.types import HttpOptions


def create_google_genai_http_options() -> HttpOptions:
    """Create SDK SSL options; call with ``asyncio.to_thread`` during startup."""
    import certifi
    from google.genai import types

    context = ssl.create_default_context(
        cafile=os.environ.get("SSL_CERT_FILE", certifi.where()),
        capath=os.environ.get("SSL_CERT_DIR"),
    )
    return types.HttpOptions(
        client_args={"verify": context},
        async_client_args={"verify": context, "ssl": context},
    )
