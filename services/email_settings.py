"""Validated SendGrid configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from email.utils import parseaddr


@dataclass(frozen=True)
class EmailSettings:
    api_key: str
    email_from: str
    email_to: str

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> EmailSettings:
        env = os.environ if environ is None else environ
        values = {
            key: env.get(key, "").strip()
            for key in ("SENDGRID_API_KEY", "EMAIL_FROM", "EMAIL_TO")
        }
        missing = [
            key
            for key, value in values.items()
            if not value
            or value.lower().startswith("your_")
            or value.lower().startswith("sg_replace")
            or "example.com" in value.lower()
            or "yourdomain" in value.lower()
        ]
        if missing:
            raise ValueError(
                "Missing or placeholder email configuration: " + ", ".join(missing)
            )
        for key in ("EMAIL_FROM", "EMAIL_TO"):
            address = parseaddr(values[key])[1]
            if (
                not address
                or address != values[key]
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address)
            ):
                raise ValueError(f"{key} must be a single valid email address")
        return cls(values["SENDGRID_API_KEY"], values["EMAIL_FROM"], values["EMAIL_TO"])
