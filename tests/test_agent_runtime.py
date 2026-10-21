import asyncio
import ssl

import pytest

from services.google_ssl import create_google_genai_http_options


@pytest.mark.asyncio
async def test_gemini_ssl_context_is_supplied_to_all_sdk_transports():
    options = await asyncio.to_thread(create_google_genai_http_options)
    context = options.client_args["verify"]
    assert isinstance(context, ssl.SSLContext)
    assert options.async_client_args["verify"] is context
    assert options.async_client_args["ssl"] is context


@pytest.mark.asyncio
async def test_google_genai_client_uses_prebuilt_context_without_sync_ssl_creation(
    monkeypatch,
):
    from google import genai

    options = await asyncio.to_thread(create_google_genai_http_options)

    def should_not_create_context(*args, **kwargs):
        raise AssertionError("SSL context creation must happen in the worker thread")

    monkeypatch.setattr(ssl, "create_default_context", should_not_create_context)
    client = genai.Client(api_key="test-key", http_options=options)
    client.close()
