"""Create and email one PDF/JSON call report after LiveKit job shutdown."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.base_agent import CallData, json_safe, safe_filename
from services.email_settings import EmailSettings
from services.groq_analysis import analyze_call
from services.intake_email import send_call_report_email
from services.pdf_report import generate_call_report_pdf

logger = logging.getLogger("post_call")
_processed_call_ids: set[str] = set()


def _write_json(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{safe_filename(report['call_metadata']['call_id'])}.json"
    serialized = json.dumps(json_safe(report), ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=output_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        with suppress(OSError):
            os.chmod(temp_name, 0o600)
        # A hard link gives an atomic create that refuses to overwrite another call.
        os.link(temp_name, path)
        os.unlink(temp_name)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return path


async def run(
    session: Any,
    data: CallData,
    *,
    transcript_snapshot: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prepare attachments and attempt one email; duplicate shutdowns are ignored."""
    del session  # The final transcript is captured from events during the live session.
    if data.call_id in _processed_call_ids:
        return {"status": "skipped", "reason": "duplicate shutdown callback"}
    _processed_call_ids.add(data.call_id)
    if data.ended_at is None:
        data.finalize(data.disconnect_reason)

    # Snapshot event-captured turns before asynchronous post-call processing begins.
    transcript = [
        dict(turn)
        for turn in (
            data.transcript if transcript_snapshot is None else transcript_snapshot
        )
    ]
    try:
        analysis = await analyze_call(transcript)
    except Exception as exc:
        logger.warning("Groq post-call analysis failed (%s)", type(exc).__name__)
        analysis = {
            "status": "failed",
            "error": type(exc).__name__,
            "summary": None,
            "extracted_fields": [],
            "follow_up_actions": [],
        }

    generated_at = datetime.now(timezone.utc)
    call_metadata = data.to_dict()
    call_metadata.pop("transcript", None)
    call_metadata["caller_phone"] = call_metadata.pop("caller_number", None)
    report: dict[str, Any] = {
        "call_metadata": call_metadata,
        "conversation_history": transcript,
        "analysis": analysis,
        "report": {
            "generated_at": generated_at,
            "status": "generating",
            "pdf_status": "pending",
            "json_status": "pending",
            "errors": [],
        },
    }
    output_dir = Path(
        os.getenv(
            "CALL_RECORDS_DIR",
            str(Path(__file__).resolve().parents[1] / "call_records"),
        )
    )
    pdf_path: str | None = None
    try:
        timeout = float(os.getenv("EMAIL_TIMEOUT_SECONDS", "15"))
        pdf_path = await asyncio.wait_for(
            asyncio.to_thread(generate_call_report_pdf, report, output_dir),
            timeout=timeout,
        )
        report["report"]["pdf_status"] = "ready"
        report["report"]["pdf_file"] = Path(pdf_path).name
    except Exception as exc:
        report["report"]["pdf_status"] = "failed"
        report["report"]["errors"].append(f"pdf:{type(exc).__name__}")
        logger.warning(
            "Call report PDF generation failed (%s); continuing with JSON",
            type(exc).__name__,
        )

    report["report"]["status"] = "ready" if pdf_path else "partial"
    data.report_generation_status = report["report"]["status"]
    final_metadata = data.to_dict()
    final_metadata.pop("transcript", None)
    final_metadata["caller_phone"] = final_metadata.pop("caller_number", None)
    report["call_metadata"] = final_metadata
    try:
        report["report"]["json_status"] = "saved"
        json_path = await asyncio.wait_for(
            asyncio.to_thread(_write_json, report, output_dir),
            timeout=float(os.getenv("EMAIL_TIMEOUT_SECONDS", "15")),
        )
        data.report_generation_status = report["report"]["status"]
    except Exception as exc:
        data.report_generation_status = "failed"
        logger.error(
            "Call report JSON serialization or persistence failed (%s)",
            type(exc).__name__,
        )
        return {"status": "failed", "reason": "json_report_unavailable"}

    enabled = os.getenv("EMAIL_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        logger.info("Post-call email skipped because EMAIL_ENABLED is false")
        data.report_generation_status = "ready_email_disabled"
        return {
            "status": "skipped",
            "reason": "email disabled",
            "json_path": str(json_path),
        }
    try:
        EmailSettings.from_env()
    except ValueError as exc:
        logger.warning("Post-call email skipped: %s", exc)
        data.report_generation_status = "ready_email_configuration_missing"
        return {"status": "skipped", "reason": str(exc), "json_path": str(json_path)}

    try:
        result = await asyncio.wait_for(
            send_call_report_email(
                report,
                pdf_path=pdf_path,
                json_path=str(json_path),
            ),
            timeout=float(os.getenv("EMAIL_TIMEOUT_SECONDS", "15")) + 2,
        )
    except asyncio.TimeoutError:
        result = {"status": "failed", "error": "email_timeout"}
    if result.get("status") == "success":
        data.report_generation_status = "email_accepted"
        logger.info(
            "Call report email accepted by SendGrid (HTTP %s)",
            result.get("http_status"),
        )
    else:
        data.report_generation_status = "email_failed"
        logger.warning(
            "Call report email completed with status %s (HTTP %s; error %s)",
            result.get("status"),
            result.get("http_status", "unavailable"),
            result.get("error", "unavailable"),
        )
    return {**result, "json_path": str(json_path), "pdf_path": pdf_path}
