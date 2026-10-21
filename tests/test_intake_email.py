import base64
import json
import logging
from typing import ClassVar

import pytest

from services.intake_email import send_call_report_email


def settings_values():
    return {
        "SENDGRID_API_KEY": "SG_unit_test_secret",
        "EMAIL_FROM": "sender@domain.test",
        "EMAIL_TO": "recipient@domain.test",
    }


class FakeSendGrid:
    requests: ClassVar[list] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, api_key):
        self.api_key = api_key
        self.client = type("HTTPClient", (), {})()

    def send(self, message):
        if self.error:
            raise self.error
        self.requests.append(message.get())
        return type("Response", (), {"status_code": 202})()


def attachment_names(request):
    return {item["filename"] for item in request["attachments"]}


@pytest.fixture(autouse=True)
def fake_sendgrid(monkeypatch):
    import sendgrid

    FakeSendGrid.requests = []
    FakeSendGrid.error = None
    monkeypatch.setattr(sendgrid, "SendGridAPIClient", FakeSendGrid)
    for key, value in settings_values().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("EMAIL_MAX_ATTACHMENT_BYTES", "100000")


def sample_report():
    return {
        "call": {
            "call_id": "call-1",
            "caller_number": "+15551234567",
            "status": "ended",
            "disconnect_reason": "caller disconnected",
            "started_at": "2026-10-19T10:00:00Z",
            "duration_seconds": 42,
        },
        "transcript": [{"speaker": "Caller", "text": "Hello", "timestamp": None}],
        "report": {"status": "ready"},
    }


@pytest.mark.asyncio
async def test_one_email_with_pdf_and_json_attachments(tmp_path):
    from sendgrid import SendGridAPIClient

    json_file = tmp_path / "report.json"
    json_file.write_text(json.dumps(sample_report()), encoding="utf-8")
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-fake-test")
    result = await send_call_report_email(sample_report(), str(pdf_file), str(json_file))
    request = SendGridAPIClient.requests[0]
    assert result["status"] == "success"
    assert len(SendGridAPIClient.requests) == 1
    assert attachment_names(request) == {
        "report.pdf",
        "call-summary.json",
    }
    json_attachment = next(
        a for a in request["attachments"] if a["filename"] == "call-summary.json"
    )
    assert base64.b64decode(json_attachment["content"]) == json_file.read_bytes()


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_missing_config_and_sendgrid_failure_are_sanitized(
    monkeypatch, tmp_path, caplog
):
    from sendgrid import SendGridAPIClient

    json_file = tmp_path / "report.json"
    json_file.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("EMAIL_TO")
    result = await send_call_report_email(sample_report(), json_path=str(json_file))
    assert result["status"] == "failed"
    monkeypatch.setenv("EMAIL_TO", "recipient@domain.test")

    class ApiError(Exception):
        status_code = 403

    SendGridAPIClient.error = ApiError("SG_unit_test_secret authorization denied")
    with caplog.at_level(logging.WARNING):
        result = await send_call_report_email(sample_report(), json_path=str(json_file))
    assert result["status"] == "failed"
    assert result["http_status"] == 403
    assert "SG_unit_test_secret" not in caplog.text


@pytest.mark.asyncio
async def test_invalid_recipient_is_rejected_before_send(monkeypatch, tmp_path):
    from sendgrid import SendGridAPIClient

    monkeypatch.setenv("EMAIL_TO", "not-an-email")
    json_file = tmp_path / "report.json"
    json_file.write_text("{}", encoding="utf-8")
    result = await send_call_report_email(sample_report(), json_path=str(json_file))
    assert result["status"] == "failed"
    assert SendGridAPIClient.requests == []


@pytest.mark.asyncio
async def test_email_renders_analysis_metadata_transcript_and_escapes_html(tmp_path):
    from sendgrid import SendGridAPIClient

    report = sample_report()
    report["call"].update({"room_name": "room-1", "agent_name": "agent-1"})
    report["analysis"] = {
        "status": "success",
        "summary": "Caller asked about <weather>.",
        "extracted_fields": [
            {"field": "Name", "value": "<b>Sam</b>", "source_quote": "<b>Sam</b>"}
        ],
        "follow_up_actions": ["Call <tomorrow>"],
    }
    report["transcript"] = [
        {"speaker": "Assistant", "timestamp": "10:00", "text": "How can I help?"},
        {
            "speaker": "Caller",
            "timestamp": "10:01",
            "text": "My name is <b>Sam</b>. <script>alert('x')</script>",
        },
        {"speaker": "Assistant", "timestamp": None, "text": "I can help."},
    ]
    report["analysis"]["extracted_fields"].append(
        {"field": "Unsupported", "value": "Nowhere", "source_quote": "Not in the call"}
    )
    report["analysis"]["extracted_fields"].append(
        {
            "field": "Phone",
            "value": "+15551234567",
            "source_quote": "My number is +15551234567",
        }
    )
    json_file = tmp_path / "report.json"
    json_file.write_text(json.dumps(report), encoding="utf-8")
    result = await send_call_report_email(report, json_path=str(json_file))
    request = SendGridAPIClient.requests[0]
    html_content = next(
        item["value"] for item in request["content"] if item["type"] == "text/html"
    )
    text_content = next(
        item["value"] for item in request["content"] if item["type"] == "text/plain"
    )
    assert result["status"] == "success"
    assert "&lt;weather&gt;" in html_content
    assert "&lt;b&gt;Sam&lt;/b&gt;" in html_content
    assert "Call summary" in html_content
    assert "Follow-up Actions" in html_content
    assert "Call metadata" in html_content
    assert (
        "The full transcript is available in the attached PDF and JSON report"
        in html_content
    )
    assert "How can I help?" not in html_content
    assert "I can help." not in html_content
    assert "Unsupported" not in html_content
    assert "Phone" not in html_content
    assert "10:00" in html_content
    assert "How can I help?" not in text_content
    assert "My name is <b>Sam</b>. <script>alert('x')</script>" not in text_content
    assert "I can help." not in text_content
    assert (
        "The full transcript is available in the attached PDF and JSON report"
        in text_content
    )
    assert "Unsupported" not in text_content
    assert "Phone" not in text_content


@pytest.mark.asyncio
async def test_final_summary_json_attachment_has_analysis_and_complete_history(
    tmp_path,
):
    from sendgrid import SendGridAPIClient

    report = {
        "call_metadata": {
            "call_id": "final-call",
            "caller_phone": "+15550001000",
            "started_at": "2026-10-19T10:00:00Z",
            "ended_at": "2026-10-19T10:00:45Z",
            "duration_seconds": 45.9,
            "status": "ended",
            "room_name": "room-final",
            "agent_name": "my-agent",
        },
        "analysis": {
            "status": "success",
            "summary": "Caller Noor asked about an appointment.",
            "extracted_fields": [
                {"field": "Name", "value": "Noor", "source_quote": "My name is Noor."}
            ],
            "follow_up_actions": [],
        },
        "conversation_history": [
            {
                "speaker": "Assistant",
                "timestamp": "2026-10-19T10:00:00Z",
                "text": "How can I help?",
            },
            {
                "speaker": "Caller",
                "timestamp": "2026-10-19T10:00:02Z",
                "text": "My name is Noor.",
            },
        ],
        "report": {"status": "ready"},
    }
    report_file = tmp_path / "final.json"
    report_file.write_text(json.dumps(report), encoding="utf-8")
    result = await send_call_report_email(report, json_path=str(report_file))
    request = SendGridAPIClient.requests[0]
    attachment = next(
        item
        for item in request["attachments"]
        if item["filename"] == "call-summary.json"
    )
    decoded = base64.b64decode(attachment["content"]).decode("utf-8")
    attached_report = json.loads(decoded)
    assert result["status"] == "success"
    assert attached_report["analysis"]["summary"] == report["analysis"]["summary"]
    assert attached_report["conversation_history"] == report["conversation_history"]
    assert len(attached_report["conversation_history"]) == 2
    assert "Noor asked about an appointment" in request["content"][0]["value"]


@pytest.mark.asyncio
async def test_failed_analysis_is_not_presented_as_summary_and_transcript_is_attached(
    tmp_path,
):
    from sendgrid import SendGridAPIClient

    report = {
        "call_metadata": {"call_id": "failed-call", "status": "ended"},
        "analysis": {
            "status": "failed",
            "error": "HTTP 504 DEADLINE_EXCEEDED",
            "summary": None,
            "extracted_fields": [],
            "follow_up_actions": [],
        },
        "conversation_history": [
            {"speaker": "Caller", "timestamp": None, "text": "Please call me."}
        ],
        "report": {"status": "partial"},
    }
    report_file = tmp_path / "failed.json"
    report_file.write_text(json.dumps(report), encoding="utf-8")
    result = await send_call_report_email(report, json_path=str(report_file))
    request = SendGridAPIClient.requests[0]
    content = request["content"][0]["value"]
    attached_json = next(
        item
        for item in request["attachments"]
        if item["filename"] == "call-summary.json"
    )
    attached_report = json.loads(base64.b64decode(attached_json["content"]))
    assert result["status"] == "success"
    assert "Analysis status: failed" in content
    assert "No Groq summary is being presented as successful" in content
    assert "Please call me." not in content
    assert attached_report["analysis"]["status"] == "failed"
    assert attached_report["conversation_history"][0]["text"] == "Please call me."
