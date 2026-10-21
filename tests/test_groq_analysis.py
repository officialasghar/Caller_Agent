import json

import pytest

import services.groq_analysis as groq_analysis
from services.groq_analysis import GroqAPIError, _error_reason, _parse_response, analyze_call


def transcript():
    return [
        {
            "speaker": "Assistant",
            "timestamp": "2026-10-20T10:00:00Z",
            "text": "How can I help?",
        },
        {
            "speaker": "Caller",
            "timestamp": "2026-10-20T10:00:02Z",
            "text": (
                "My name is Sam. I am 31. My email is sam@example.com. "
                "I live in Lahore. I need an appointment."
            ),
        },
        {
            "speaker": "Assistant",
            "timestamp": None,
            "text": "I can arrange a callback.",
        },
    ]


def test_parse_response_keeps_only_supported_caller_information_fields():
    parsed = _parse_response(
        json.dumps(
            {
                "summary": "Sam requests an appointment.",
                "extracted_fields": [
                    {
                        "field": "Full name",
                        "value": "Sam",
                        "source_quote": "My name is Sam",
                    },
                    {"field": "Age", "value": "31", "source_quote": "I am 31"},
                    {
                        "field": "Email",
                        "value": "sam@example.com",
                        "source_quote": "My email is sam@example.com",
                    },
                    {
                        "field": "City",
                        "value": "Lahore",
                        "source_quote": "I live in Lahore",
                    },
                    {
                        "field": "Topic",
                        "value": "appointment",
                        "source_quote": "I need an appointment",
                    },
                    {
                        "field": "Phone",
                        "value": "123456789",
                        "source_quote": "My phone is 123456789",
                    },
                    {
                        "field": "City",
                        "value": "Karachi",
                        "source_quote": "I can arrange a callback",
                    },
                ],
                "follow_up_actions": ["Arrange a callback"],
            }
        ),
        transcript(),
    )
    assert parsed["extracted_fields"] == [
        {"field": "Name", "value": "Sam", "source_quote": "My name is Sam"},
        {"field": "Age", "value": "31", "source_quote": "I am 31"},
        {
            "field": "Email",
            "value": "sam@example.com",
            "source_quote": "My email is sam@example.com",
        },
        {"field": "City", "value": "Lahore", "source_quote": "I live in Lahore"},
        {
            "field": "Topic",
            "value": "appointment",
            "source_quote": "I need an appointment",
        },
    ]
    assert parsed["follow_up_actions"] == ["Arrange a callback"]


def test_provider_error_details_are_classified_without_echoing_raw_text():
    body = json.dumps(
        {
            "error": {
                "type": "permissions_error",
                "code": "model_permission_blocked_project",
                "message": "Model restricted; sensitive details omitted",
            }
        }
    ).encode()
    assert _error_reason(403, body) == "model_permission_blocked"
    assert _error_reason(403, b"invalid response") == "permission_denied"
    assert _error_reason(401, b"") == "invalid_credentials"


@pytest.mark.asyncio
async def test_analyze_uses_groq_and_returns_structured_result(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GROQ_POST_CALL_MODEL", "configured-groq-model")
    captured = {}

    def fake_generate(key, model, turns, timeout):
        captured.update(key=key, model=model, turns=turns, timeout=timeout)
        return json.dumps(
            {
                "summary": "Sam needs an appointment.",
                "extracted_fields": [
                    {"field": "Name", "value": "Sam", "source_quote": "My name is Sam"}
                ],
                "follow_up_actions": [],
            }
        )

    monkeypatch.setattr(groq_analysis, "_generate_sync", fake_generate)
    result = await analyze_call(transcript())
    assert result["status"] == "success"
    assert result["model"] == "configured-groq-model"
    assert captured["key"] == "gsk_test"
    assert captured["turns"] == transcript()


@pytest.mark.asyncio
async def test_analyze_uses_gpt_oss_as_the_default_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.delenv("GROQ_POST_CALL_MODEL", raising=False)
    captured = {}

    def fake_generate(key, model, turns, timeout):
        captured["model"] = model
        return json.dumps(
            {
                "summary": "Caller needs a callback.",
                "extracted_fields": [],
                "follow_up_actions": [],
            }
        )

    monkeypatch.setattr(groq_analysis, "_generate_sync", fake_generate)
    result = await analyze_call(transcript())
    assert result["status"] == "success"
    assert captured["model"] == "openai/gpt-oss-120b"


@pytest.mark.asyncio
async def test_missing_groq_key_and_empty_history_skip_request(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert (await analyze_call(transcript()))["error"] == "groq_api_key_missing"
    assert (await analyze_call([]))["error"] == "transcript_empty"


@pytest.mark.asyncio
async def test_permanent_error_is_not_retried(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    attempts = 0

    def fail(*args):
        nonlocal attempts
        attempts += 1
        raise GroqAPIError(401, "invalid_credentials")

    monkeypatch.setattr(groq_analysis, "_generate_sync", fail)
    result = await analyze_call(transcript())
    assert result["status"] == "failed"
    assert result["error"] == "HTTP 401 invalid_credentials"
    assert attempts == 1


@pytest.mark.asyncio
async def test_transient_error_retries(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GROQ_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("GROQ_RETRY_BASE_DELAY_SECONDS", "0")
    attempts = 0

    def succeed_second(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise GroqAPIError(429)
        return json.dumps(
            {"summary": "ok", "extracted_fields": [], "follow_up_actions": []}
        )

    monkeypatch.setattr(groq_analysis, "_generate_sync", succeed_second)
    result = await analyze_call(transcript())
    assert result["status"] == "success"
    assert attempts == 2
