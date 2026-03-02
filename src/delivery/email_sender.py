"""Send the HTML digest via SMTP email."""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_digest_email(html: str, subject: str, recipients: list[str]) -> None:
    """Send *html* as an email to *recipients* using SMTP credentials from env vars.

    Required env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM.
    Uses STARTTLS on port 587 (default) or SSL on port 465.
    """
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("EMAIL_FROM", user)

    if not all([host, user, password]):
        raise RuntimeError(
            "Email not configured — set SMTP_HOST, SMTP_USER, and SMTP_PASSWORD in .env"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    if port == 465:
        smtp = smtplib.SMTP_SSL(host, port)
    else:
        smtp = smtplib.SMTP(host, port)
        smtp.starttls()

    with smtp:
        smtp.login(user, password)
        smtp.sendmail(sender, recipients, msg.as_string())

    logger.info("Digest email sent to %s", ", ".join(recipients))
