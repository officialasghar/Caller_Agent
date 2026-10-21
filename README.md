# LiveKit Caller Agent

Voice calls are handled by Gemini Realtime. When a call ends, Groq generates a
summary and extracts supported caller information, then SendGrid sends a PDF and
JSON report. Recordings are not collected or attached.

## Project structure

```text
Task_Agent/
|-- main.py                     # LiveKit command-line entrypoint
|-- agents/
|   `-- voice.py                # Voice-agent configuration
|-- models/
|   `-- call_data.py            # Per-call state and JSON serialization
|-- services/
|   |-- groq_analysis.py        # Post-call summary and field extraction
|   |-- intake_email.py         # SendGrid email delivery
|   |-- pdf_report.py           # PDF report rendering
|   |-- post_call.py            # Post-call workflow
|   |-- sip_metadata.py         # SIP caller metadata
|   |-- email_settings.py       # Email configuration validation
|   `-- google_ssl.py           # Google client SSL setup
|-- tools/                      # Agent callable tools
|-- web/                        # Optional browser client
|-- tests/                      # Automated tests
|-- .env.example
|-- pyproject.toml
`-- taskfile.yaml
```

## Configure and run

```powershell
uv sync
Copy-Item .env.example .env.local
lk agent dev main.py
```

Set LiveKit, Google, Groq, doctor-transfer, and SendGrid values in `.env.local`.
The post-call model defaults to `openai/gpt-oss-120b` through Groq.

The extracted-information section includes only caller-backed `Name`, `Age`,
`Email`, `City`, and `Topic` values. Unavailable fields are omitted. The email
contains the summary and metadata; the complete transcript is attached in the
PDF and JSON report.

Call reports are written to `call_records/` when a call ends. The directory is
created automatically and is excluded from Git.
