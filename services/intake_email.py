"""Send one SendGrid message with the available call report attachments."""

from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from models.base_agent import json_safe
from services.email_settings import EmailSettings
from services.groq_analysis import normalize_extracted_field

logger = logging.getLogger("post_call.email")

# The SendGrid SDK logs full headers and mail payloads at DEBUG, including the
# authorization token, transcript, and base64 attachments. Keep those details
# out of application logs even when the root logger is configured for DEBUG.
logging.getLogger("python_http_client.client").setLevel(logging.WARNING)


def _send_sync(
    report: dict[str, Any],
    pdf_path: str | None,
    json_path: str,
) -> dict[str, Any]:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (
        Attachment,
        Disposition,
        FileContent,
        FileName,
        FileType,
        Mail,
    )

    settings = EmailSettings.from_env()
    call = report.get("call_metadata") or report.get("call", {})
    turns = report.get("conversation_history", report.get("transcript")) or []
    call_id = str(call.get("call_id", "Unavailable"))
    analysis = report.get("analysis") or {}
    analysis_succeeded = analysis.get("status") == "success" and bool(
        analysis.get("summary")
    )
    source_texts = [
        re.sub(r"\s+", " ", str(turn.get("text") or "")).strip().casefold()
        for turn in turns
        if str(turn.get("speaker") or "").casefold() == "caller"
    ]
    extracted = []
    if analysis_succeeded:
        for item in analysis.get("extracted_fields") or []:
            if not isinstance(item, dict):
                continue
            field, value, quote = (
                str(item.get("field") or "").strip(),
                str(item.get("value") or "").strip(),
                str(item.get("source_quote") or "").strip(),
            )
            normalized_quote = re.sub(r"\s+", " ", quote).casefold()
            normalized_value = re.sub(r"\s+", " ", value).casefold()
            approved_field = normalize_extracted_field(field)
            if (
                approved_field
                and value
                and quote
                and normalized_value
                not in {"unknown", "n/a", "na", "not provided", "none"}
                and any(normalized_quote in source for source in source_texts)
                and all(
                    re.sub(r"\s+", " ", part).strip().casefold() in normalized_quote
                    for part in value.split(" / ")
                )
            ):
                extracted.append({"field": approved_field, "value": value})
    caller_name = next(
        (
            str(item.get("value"))
            for item in extracted
            if str(item.get("field", "")) == "Name" and item.get("value")
        ),
        None,
    )
    caller = str(
        caller_name or call.get("caller_phone") or call.get("caller_number") or call_id
    )
    subject = f"Post-Call Summary - {caller}"

    limit = max(1, int(os.getenv("EMAIL_MAX_ATTACHMENT_BYTES", "15000000")))
    total = 0
    attachments = []
    omitted: list[str] = []

    def add_file(
        path: Path,
        content_type: str,
        filename: str,
        *,
        required: bool,
        report_omission: bool = True,
    ) -> bool:
        nonlocal total
        if not path.is_file():
            if required:
                raise FileNotFoundError("Call report JSON attachment is unavailable")
            if report_omission:
                omitted.append(filename)
            return False
        size = path.stat().st_size
        if size <= 0 or size > limit - total:
            if required:
                raise ValueError(
                    f"Required {content_type} attachment exceeds the configured size limit"
                )
            if report_omission:
                omitted.append(filename)
            return False
        with path.open("rb") as stream:
            content = stream.read(limit - total + 1)
        if len(content) != size or len(content) > limit - total:
            if required:
                raise ValueError(
                    f"Required {content_type} attachment exceeds the configured size limit"
                )
            if report_omission:
                omitted.append(filename)
            return False
        if content_type == "application/pdf" and not content.startswith(b"%PDF-"):
            if report_omission:
                omitted.append(filename)
            return False
        total += size
        attachments.append(
            Attachment(
                FileContent(base64.b64encode(content).decode("ascii")),
                FileName(filename),
                FileType(content_type),
                Disposition("attachment"),
            )
        )
        return True

    add_file(Path(json_path), "application/json", "call-summary.json", required=True)
    if pdf_path:
        add_file(Path(pdf_path), "application/pdf", Path(pdf_path).name, required=False)
    else:
        omitted.append("call-report.pdf")

    omission_note = (
        "Omitted attachments: " + ", ".join(dict.fromkeys(omitted)) + "."
        if omitted
        else ""
    )
    metadata = [
        ("Call ID", call.get("call_id")),
        ("Caller phone", call.get("caller_phone", call.get("caller_number"))),
        ("Started", call.get("started_at")),
        ("Ended", call.get("ended_at")),
        (
            "Duration",
            f"{call['duration_seconds']} seconds"
            if call.get("duration_seconds") is not None
            else None,
        ),
        ("Status", call.get("status")),
        ("Disconnect reason", call.get("disconnect_reason")),
        ("Room", call.get("room_name")),
        ("Agent", call.get("agent_name")),
    ]
    metadata = [
        (key, value)
        for key, value in metadata
        if value not in (None, "", "Unavailable")
    ]

    body = "\n".join(
        [
            "Post-call summary",
            f"Call ID: {call_id}",
            f"Status: {call.get('status') or 'Unavailable'}",
            f"Disconnect reason: {call.get('disconnect_reason') or 'Unavailable'}",
            f"Duration: {call.get('duration_seconds') if call.get('duration_seconds') is not None else 'Unavailable'} seconds",
            f"Analysis status: {analysis.get('status', 'unavailable')}",
            (
                f"Summary: {analysis['summary']}"
                if analysis_succeeded
                else "Analysis failed or was unavailable. No Groq summary is being presented as successful."
            ),
            "Extracted caller information:",
            *(
                [f"- {item['field']}: {item['value']}" for item in extracted]
                or ["(No supported fields were extracted)"]
            ),
            "",
            "Call metadata:",
            *[f"- {key}: {value}" for key, value in metadata],
            *[
                f"Follow-up action: {action}"
                for action in (
                    analysis.get("follow_up_actions", []) if analysis_succeeded else []
                )
                if action
            ],
            "The full transcript is available in the attached PDF and JSON report.",
            f"Report status: {report.get('report', {}).get('status', 'Unavailable')}",
            omission_note,
        ]
    ).strip()

    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    summary_html = (
        f"<div style='padding:16px;background:#e8eaff;border-left:5px solid #4f46e5;border-radius:6px;line-height:1.55'>{esc(analysis['summary'])}</div>"
        if analysis_succeeded
        else "<div style='padding:16px;background:#fff4e5;border-left:5px solid #d97706;border-radius:6px'>Analysis failed or was unavailable. No Groq summary was generated. The full transcript is available in the attached PDF and JSON report.</div>"
    )
    field_rows = "".join(
        f"<tr><th style='text-align:left;padding:6px'>{esc(item['field'])}</th>"
        f"<td style='padding:6px'>{esc(item['value'])}</td></tr>"
        for item in extracted
        if item.get("field") and item.get("value")
    )
    fields_html = (
        "<table role='presentation' style='border-collapse:collapse;width:100%;border:1px solid #c7d2fe'>"
        "<tr style='background:#4338ca;color:#fff'><th style='text-align:left;padding:9px'>Field</th>"
        "<th style='text-align:left;padding:9px'>Extracted value</th></tr>"
        + field_rows
        + "</table>"
        if field_rows
        else "<p>No supported caller details were extracted.</p>"
    )
    actions = (
        [a for a in analysis.get("follow_up_actions", []) if a]
        if analysis_succeeded
        else []
    )
    followup_html = (
        "<h2>Follow-up Actions</h2><ul>"
        + "".join(f"<li>{esc(action)}</li>" for action in actions)
        + "</ul>"
        if actions
        else ""
    )
    metadata_html = (
        "<table role='presentation' style='border-collapse:collapse;width:100%;border:1px solid #dbe3ef'>"
        + "".join(
            f"<tr style='background:{'#f1f5f9' if index % 2 else '#ffffff'}'>"
            f"<th style='text-align:left;padding:8px;border-bottom:1px solid #dbe3ef;width:32%'>{esc(key)}</th>"
            f"<td style='padding:8px;border-bottom:1px solid #dbe3ef;overflow-wrap:anywhere'>{esc(value)}</td></tr>"
            for index, (key, value) in enumerate(metadata)
        )
        + "</table>"
    )
    html_body = (
        "<html><body style='margin:0;padding:18px;background:#eef2ff;font-family:Arial,Helvetica,sans-serif;color:#172033'>"
        "<table role='presentation' style='width:100%;max-width:760px;margin:auto;border-collapse:collapse;background:#fff'>"
        "<tr><td style='padding:22px;background:#1e1b4b;color:#fff'><h1 style='margin:0;font-size:24px'>LiveKit Call Report</h1>"
        f"<p style='margin:7px 0 0;color:#c7d2fe'>Analysis status: {esc(analysis.get('status', 'unavailable'))}</p></td></tr>"
        f"<tr><td style='padding:18px'><h2 style='color:#312e81'>Call summary</h2>{summary_html}"
        f"<h2 style='color:#312e81;margin-top:24px'>Extracted information</h2>{fields_html}"
        f"{followup_html}<h2 style='color:#312e81;margin-top:24px'>Call metadata</h2>{metadata_html}"
        "<p style='margin-top:24px;color:#475569'>The full transcript is available in the attached PDF and JSON report.</p>"
        f"<p style='color:#9a3412'>{esc(omission_note)}</p></td></tr></table></body></html>"
    )
    message = Mail(
        from_email=settings.email_from,
        to_emails=settings.email_to,
        subject=subject,
        plain_text_content=body,
        html_content=html_body,
    )
    for attachment in attachments:
        message.add_attachment(attachment)
    client = SendGridAPIClient(settings.api_key)
    client.client.timeout = max(
        1, int(float(os.getenv("EMAIL_TIMEOUT_SECONDS", "15")))
    )
    response = client.send(message)
    status_code = int(response.status_code)
    if 200 <= status_code < 300:
        return {"status": "success", "http_status": status_code, "omitted": omitted}
    return {"status": "failed", "http_status": status_code, "omitted": omitted}


async def send_call_report_email(
    report: dict[str, Any],
    pdf_path: str | None = None,
    json_path: str | None = None,
) -> dict[str, Any]:
    """Send one bounded SendGrid request. No credentials or report body are logged."""
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    try:
        if json_path is None:
            temporary_directory = tempfile.TemporaryDirectory(prefix="call-report-")
            json_path = str(Path(temporary_directory.name) / "call-summary.json")
            Path(json_path).write_text(
                json.dumps(json_safe(report), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return await asyncio.wait_for(
            asyncio.to_thread(_send_sync, report, pdf_path, json_path),
            timeout=float(os.getenv("EMAIL_TIMEOUT_SECONDS", "15")) + 1,
        )
    except asyncio.TimeoutError:
        logger.warning("Post-call SendGrid request timed out")
        return {"status": "failed", "error": "timeout"}
    except ValueError as exc:
        logger.warning("Post-call email configuration or attachment issue: %s", exc)
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        logger.warning("Post-call email failed (%s)", type(exc).__name__)
        response = getattr(exc, "status_code", None)
        result: dict[str, Any] = {
            "status": "failed",
            "error": type(exc).__name__,
        }
        if response is not None:
            result["http_status"] = int(response)
        return result
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()
