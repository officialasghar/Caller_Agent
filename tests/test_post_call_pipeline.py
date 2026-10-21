import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from models.base_agent import CallData, resolve_room_sid
from services.pdf_report import generate_call_report_pdf
from services.post_call import _processed_call_ids, run


def test_call_data_is_json_safe_and_transcript_updates_without_duplicates():
    data = CallData()
    data.add_transcript_item(
        item_id="turn-1", speaker="Caller", text="Hi", timestamp=None
    )
    data.add_transcript_item(
        item_id="turn-1", speaker="Caller", text="Hi there", timestamp=None
    )
    result = data.to_dict()
    assert result["started_at"].endswith("Z")
    assert len(result["transcript"]) == 1
    assert result["transcript"][0]["text"] == "Hi there"


def test_awaitable_cannot_leak_into_json():
    data = CallData()

    async def pending():
        return "not serializable"

    data.transcript.append({"speaker": "Caller", "text": pending()})
    with pytest.raises(TypeError, match="Awaitable"):
        data.to_dict()


@pytest.mark.asyncio
async def test_async_room_sid_is_awaited_and_timeout_is_safe():
    class Room:
        @property
        async def sid(self):
            return "RM_test"

    class StalledRoom:
        @property
        async def sid(self):
            return await __import__("asyncio").sleep(1, result="late")

    assert await resolve_room_sid(Room()) == "RM_test"
    assert await resolve_room_sid(StalledRoom(), timeout_seconds=0.001) is None


def test_pdf_contains_call_and_transcript_and_supports_pages(tmp_path):
    report = {
        "call": {
            "call_id": "test-call",
            "caller_number": "+15551234567",
            "caller_number_original": "+1 555 123 4567",
            "status": "ended",
            "disconnect_reason": "caller left",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 23,
            "room_name": "test-room",
            "room_sid": "RM_test",
            "sip_identity": "sip_test",
            "sip_call_id": "CA_test",
            "sip_trunk_id": "ST_test",
            "agent_name": "test-agent",
        },
        "transcript": [
            {"speaker": "Caller", "text": "Hi & welcome", "timestamp": None}
        ],
        "report": {"generated_at": datetime.now(timezone.utc).isoformat()},
    }
    path = generate_call_report_pdf(report, tmp_path)
    with open(path, "rb") as pdf_file:
        assert pdf_file.read().startswith(b"%PDF")


@pytest.mark.asyncio
async def test_shutdown_processing_saves_json_and_ignores_duplicate(
    monkeypatch, tmp_path
):
    _processed_call_ids.clear()
    monkeypatch.setenv("CALL_RECORDS_DIR", str(tmp_path))
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    data = CallData(room_name="room", started_at=datetime.now(timezone.utc))
    data.add_transcript_item(
        item_id="x", speaker="Caller", text="Hello", timestamp=None
    )
    data.finalize("caller disconnected")
    result = await run(None, data)
    json_path = tmp_path / f"{data.call_id}.json"
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert result["status"] == "skipped"
    assert report["report"]["json_status"] == "saved"
    assert report["conversation_history"][0]["text"] == "Hello"
    assert report["call_metadata"]["call_id"] == data.call_id
    assert report["analysis"]["status"] == "failed"
    assert list(tmp_path.glob("*.pdf"))
    duplicate = await run(None, data)
    assert duplicate["reason"] == "duplicate shutdown callback"
    assert len(list(tmp_path.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_pdf_failure_falls_back_to_json_email(monkeypatch, tmp_path):
    import services.post_call as post_call

    _processed_call_ids.clear()
    monkeypatch.setenv("CALL_RECORDS_DIR", str(tmp_path))
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SENDGRID_API_KEY", "SG_test")
    monkeypatch.setenv("EMAIL_FROM", "sender@domain.test")
    monkeypatch.setenv("EMAIL_TO", "recipient@domain.test")
    monkeypatch.setattr(
        post_call,
        "generate_call_report_pdf",
        lambda report, output: (_ for _ in ()).throw(RuntimeError("pdf unavailable")),
    )
    captured = {}

    async def fake_email(report, pdf_path=None, json_path=None):
        captured["pdf_path"] = pdf_path
        captured["json_path"] = json_path
        return {"status": "success", "http_status": 202}

    monkeypatch.setattr(post_call, "send_call_report_email", fake_email)
    data = CallData()
    data.finalize("caller disconnected")
    result = await post_call.run(None, data)
    assert result["status"] == "success"
    assert captured["pdf_path"] is None
    saved = json.loads(Path(captured["json_path"]).read_text(encoding="utf-8"))
    assert saved["report"]["pdf_status"] == "failed"


@pytest.mark.asyncio
async def test_shutdown_analyzes_snapshot_and_includes_analysis_in_email_json(
    monkeypatch, tmp_path
):
    import services.post_call as post_call

    _processed_call_ids.clear()
    monkeypatch.setenv("CALL_RECORDS_DIR", str(tmp_path))
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    for key, value in {
        "SENDGRID_API_KEY": "SG_test",
        "EMAIL_FROM": "sender@domain.test",
        "EMAIL_TO": "recipient@domain.test",
    }.items():
        monkeypatch.setenv(key, value)
    data = CallData()
    data.add_transcript_item(
        item_id="caller-1", speaker="Caller", text="My name is Noor.", timestamp=None
    )
    captured = {}

    async def fake_analysis(turns):
        captured["turns"] = [dict(turn) for turn in turns]
        return {
            "status": "success",
            "summary": "Caller introduced herself.",
            "extracted_fields": [
                {"field": "Name", "value": "Noor", "source_quote": "My name is Noor."}
            ],
            "follow_up_actions": [],
        }

    async def fake_email(report, pdf_path=None, json_path=None):
        captured["report"] = report
        captured["json_path"] = json_path
        return {"status": "success", "http_status": 202}

    monkeypatch.setattr(post_call, "analyze_call", fake_analysis)
    monkeypatch.setattr(post_call, "send_call_report_email", fake_email)
    result = await post_call.run(None, data)
    assert result["status"] == "success"
    assert captured["turns"][0]["text"] == "My name is Noor."
    assert captured["report"]["analysis"]["summary"] == "Caller introduced herself."
    saved = json.loads(Path(captured["json_path"]).read_text(encoding="utf-8"))
    assert saved["analysis"]["extracted_fields"][0]["value"] == "Noor"
    assert [turn["text"] for turn in saved["conversation_history"]] == [
        "My name is Noor."
    ]
