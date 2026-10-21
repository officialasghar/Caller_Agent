from pypdf import PdfReader

from services.pdf_report import generate_call_report_pdf


def test_pdf_includes_summary_fields_metadata_and_every_transcript_turn(tmp_path):
    turns = []
    for index in range(30):
        speaker = "Caller" if index % 2 else "Assistant"
        turns.append(
            {
                "speaker": speaker,
                "timestamp": f"2026-10-20T10:{index:02d}:00Z",
                "text": f"{speaker}-turn-{index:02d} "
                + ("long conversation text " * 18),
            }
        )
    long_turn = "long-message-start " + ("word " * 4000) + " long-message-end"
    turns.append({"speaker": "Caller", "timestamp": None, "text": long_turn})
    turns[1]["text"] = "My name is Noor. Caller-turn-01 " + (
        "long conversation text " * 18
    )
    turns[1]["text"] += " My number is +15550001000."

    report = {
        "call_metadata": {
            "call_id": "pdf-transcript-test",
            "caller_phone": "+15550001000",
            "started_at": "2026-10-20T10:00:00Z",
            "duration_seconds": 45,
            "status": "ended",
        },
        "analysis": {
            "status": "success",
            "summary": "Caller asked to schedule an appointment.",
            "extracted_fields": [
                {
                    "field": "Name",
                    "value": "Noor",
                    "source_quote": "My name is Noor.",
                },
                {
                    "field": "Unsupported",
                    "value": "Unknown",
                    "source_quote": "Not in the call",
                },
                {
                    "field": "Phone",
                    "value": "+15550001000",
                    "source_quote": "My number is +15550001000",
                },
            ],
            "follow_up_actions": ["Call back tomorrow."],
        },
        "conversation_history": turns,
        "report": {"generated_at": "2026-10-20T10:01:00Z"},
    }
    path = generate_call_report_pdf(report, tmp_path)
    reader = PdfReader(path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert len(reader.pages) > 1
    assert "Caller asked to schedule an appointment." in text
    assert "Call ID" in text and "pdf-transcript-test" in text
    assert "Name" in text and "Noor" in text
    assert "Unsupported" not in text
    assert "Phone" not in text
    assert "Call back tomorrow." in text
    for index in range(30):
        assert f"turn-{index:02d}" in text
    assert "long-message-start" in text
    assert "long-message-end" in text
    assert sum("Time" in (page.extract_text() or "") for page in reader.pages) > 1


def test_groq_failure_still_keeps_full_transcript_in_pdf(tmp_path):
    report = {
        "call_metadata": {"call_id": "failed-analysis", "status": "ended"},
        "analysis": {
            "status": "failed",
            "summary": None,
            "extracted_fields": [],
            "follow_up_actions": [],
        },
        "conversation_history": [
            {"speaker": "Caller", "timestamp": None, "text": "The first full turn."},
            {
                "speaker": "Assistant",
                "timestamp": None,
                "text": "The second full turn.",
            },
        ],
    }
    path = generate_call_report_pdf(report, tmp_path)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    assert "Analysis failed" in text
    assert "The first full turn." in text
    assert "The second full turn." in text
