"""Readable, multi-page PDF renderer for complete post-call reports."""

from __future__ import annotations

import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from models.base_agent import safe_filename
from services.groq_analysis import normalize_extracted_field

_INDIGO = colors.HexColor("#312e81")
_PALE = colors.HexColor("#eef2ff")
_GRID = colors.HexColor("#cbd5e1")


def _display(value: Any) -> str:
    return "" if value is None or value == "" or value == "Unavailable" else str(value)


def _supported_fields(
    analysis: dict[str, Any], turns: list[dict[str, Any]]
) -> list[dict[str, str]]:
    if analysis.get("status") != "success":
        return []
    source = [
        re.sub(r"\s+", " ", str(turn.get("text") or "")).strip().casefold()
        for turn in turns
        if str(turn.get("speaker") or "").casefold() == "caller"
    ]
    supported = []
    for item in analysis.get("extracted_fields") or []:
        if not isinstance(item, dict):
            continue
        field, value, quote = (
            str(item.get("field") or "").strip(),
            str(item.get("value") or "").strip(),
            str(item.get("source_quote") or "").strip(),
        )
        approved_field = normalize_extracted_field(field)
        normalized_quote = re.sub(r"\s+", " ", quote).strip().casefold()
        if (
            approved_field
            and value
            and quote
            and normalized_quote in " ".join(source)
            and all(
                re.sub(r"\s+", " ", part).strip().casefold() in normalized_quote
                for part in value.split(" / ")
            )
        ):
            supported.append({"field": approved_field, "value": value})
    return supported


def generate_call_report_pdf(report: dict[str, Any], output_dir: str | Path) -> str:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    call = report.get("call_metadata") or report["call"]
    turns = report.get("conversation_history", report.get("transcript")) or []
    analysis = report.get("analysis") or {}
    destination = output / f"{safe_filename(str(call['call_id']))}.pdf"
    fd, temp_name = tempfile.mkstemp(prefix=".call-report-", suffix=".pdf", dir=output)
    os.close(fd)
    try:
        font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        font_name = "Helvetica"
        if font_path.is_file():
            if "CallReportVera" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("CallReportVera", str(font_path)))
            font_name = "CallReportVera"
        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            "CallReportBody",
            parent=styles["BodyText"],
            fontName=font_name,
            leading=14,
            alignment=TA_LEFT,
            wordWrap="CJK",
            splitLongWords=1,
        )
        title = ParagraphStyle(
            "CallReportTitle", parent=styles["Title"], textColor=_INDIGO
        )
        section = ParagraphStyle(
            "CallReportSection",
            parent=styles["Heading2"],
            fontName=font_name,
            textColor=_INDIGO,
            spaceBefore=12,
            spaceAfter=7,
        )
        small = ParagraphStyle("CallReportSmall", parent=body, fontSize=8, leading=10)
        story = [Paragraph("LiveKit Call Report", title), Spacer(1, 0.12 * inch)]

        metadata = [
            ("Call ID", call.get("call_id")),
            ("Caller phone", call.get("caller_phone", call.get("caller_number"))),
            ("Original caller number", call.get("caller_number_original")),
            ("Status", call.get("status")),
            ("Disconnect reason", call.get("disconnect_reason")),
            ("Started", call.get("started_at")),
            ("Ended", call.get("ended_at")),
            (
                "Duration",
                f"{call['duration_seconds']} seconds"
                if call.get("duration_seconds") is not None
                else None,
            ),
            ("Room", call.get("room_name")),
            ("Room SID", call.get("room_sid")),
            ("SIP identity", call.get("sip_identity")),
            ("SIP call ID", call.get("sip_call_id")),
            ("SIP trunk", call.get("sip_trunk_id")),
            ("Agent", call.get("agent_name")),
            ("Transfer result", call.get("transfer_result")),
        ]
        metadata = [
            (key, _display(value)) for key, value in metadata if _display(value)
        ]
        metadata_rows = [
            [
                Paragraph(f"<b>{escape(str(key))}</b>", body),
                Paragraph(escape(str(value)), body),
            ]
            for key, value in metadata
        ]
        if metadata_rows:
            table = Table(
                metadata_rows, colWidths=[1.6 * inch, 5.2 * inch], hAlign="LEFT"
            )
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, -1), _PALE),
                        (
                            "ROWBACKGROUNDS",
                            (1, 0),
                            (-1, -1),
                            [colors.white, colors.HexColor("#f8fafc")],
                        ),
                        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            story.extend([Paragraph("Call metadata", section), table])

        story.append(Paragraph("Call summary", section))
        summary = (
            analysis.get("summary") if analysis.get("status") == "success" else None
        )
        summary_text = (
            _display(summary)
            or f"Analysis {escape(str(analysis.get('status', 'unavailable')))}. No Groq summary was generated."
        )
        summary_table = Table(
            [[Paragraph(escape(summary_text).replace("\n", "<br/>"), body)]],
            colWidths=[6.8 * inch],
        )
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _PALE),
                    ("BOX", (0, 0), (-1, -1), 1, _INDIGO),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ]
            )
        )
        story.append(summary_table)

        story.append(Paragraph("Extracted information", section))
        extracted = _supported_fields(analysis, turns)
        extracted_rows = [
            [Paragraph("<b>Field</b>", body), Paragraph("<b>Extracted value</b>", body)]
        ]
        extracted_rows.extend(
            [
                [
                    Paragraph(escape(item["field"]), body),
                    Paragraph(escape(item["value"]), body),
                ]
                for item in extracted
            ]
        )
        extracted_table = Table(
            extracted_rows,
            colWidths=[2.3 * inch, 4.5 * inch],
            repeatRows=1,
            hAlign="LEFT",
        )
        extracted_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _INDIGO),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#eef2ff")],
                    ),
                    ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(extracted_table)

        actions = (
            [
                str(action).strip()
                for action in analysis.get("follow_up_actions", [])
                if str(action).strip()
            ]
            if analysis.get("status") == "success"
            else []
        )
        if actions:
            story.append(Paragraph("Follow-up actions", section))
            story.extend(Paragraph(f"• {escape(action)}", body) for action in actions)

        story.extend([Paragraph("Full conversation transcript", section)])
        transcript_rows = [
            [
                Paragraph("<b>Time</b>", small),
                Paragraph("<b>Speaker</b>", small),
                Paragraph("<b>Conversation</b>", small),
            ]
        ]
        for turn in turns:
            speaker = str(turn.get("speaker") or "Unknown speaker")
            timestamp = _display(turn.get("timestamp")) or "—"
            text = escape(str(turn.get("text") or "")).replace("\n", "<br/>")
            transcript_rows.append(
                [
                    Paragraph(escape(timestamp), small),
                    Paragraph(f"<b>{escape(speaker)}</b>", small),
                    Paragraph(text, body),
                ]
            )
        if len(transcript_rows) == 1:
            transcript_rows.append(
                [
                    Paragraph("—", small),
                    Paragraph("—", small),
                    Paragraph("No conversation transcript was captured.", body),
                ]
            )
        transcript_table = LongTable(
            transcript_rows,
            colWidths=[1.15 * inch, 0.9 * inch, 4.75 * inch],
            repeatRows=1,
            splitByRow=1,
            splitInRow=1,
            hAlign="LEFT",
        )
        transcript_style = [
            ("BACKGROUND", (0, 0), (-1, 0), _INDIGO),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.35, _GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]
        for row, turn in enumerate(turns, start=1):
            bg = (
                colors.HexColor("#eff6ff")
                if turn.get("speaker") == "Caller"
                else colors.HexColor("#f0fdf4")
            )
            transcript_style.append(("BACKGROUND", (0, row), (-1, row), bg))
        transcript_table.setStyle(TableStyle(transcript_style))
        story.append(transcript_table)
        generated = _display((report.get("report") or {}).get("generated_at"))
        if generated:
            story.extend(
                [
                    Spacer(1, 0.1 * inch),
                    Paragraph(f"Report generated: {escape(generated)}", small),
                ]
            )

        SimpleDocTemplate(
            temp_name,
            pagesize=letter,
            rightMargin=0.65 * inch,
            leftMargin=0.65 * inch,
            topMargin=0.55 * inch,
            bottomMargin=0.55 * inch,
            title="LiveKit Call Report",
        ).build(story)
        os.replace(temp_name, destination)
        with suppress(OSError):
            os.chmod(destination, 0o600)
        return str(destination)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise
