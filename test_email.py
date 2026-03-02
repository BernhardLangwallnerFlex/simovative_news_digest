#!/usr/bin/env python3
"""Quick test script: load an existing digest HTML and send it via email."""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from config import EMAIL_RECIPIENTS
from src.delivery.email_sender import send_digest_email

DEFAULT_DIGEST = "/tmp/news_digest/processed/2026-03-01/digest_2026-03-01.html"


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIGEST)

    if not path.exists():
        print(f"Digest not found: {path}")
        return 1

    if not EMAIL_RECIPIENTS:
        print("No EMAIL_RECIPIENTS configured in .env")
        return 1

    html = path.read_text(encoding="utf-8")
    date_part = path.stem.replace("digest_", "")
    subject = f"[TEST] News Digest {date_part}"

    print(f"Sending {path.name} ({len(html)} bytes) to {', '.join(EMAIL_RECIPIENTS)}")
    send_digest_email(html=html, subject=subject, recipients=EMAIL_RECIPIENTS)
    print("Sent successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
