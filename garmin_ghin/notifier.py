"""Email notifier for sending scorecard confirmations."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import SMTPConfig
from .scorecard import Scorecard

logger = logging.getLogger(__name__)


def send_scorecard_email(smtp_config: SMTPConfig, scorecard: Scorecard) -> None:
    """Send a scorecard summary email for review."""
    subject = (
        f"Golf Scorecard: {scorecard.course_name} "
        f"({scorecard.date_played.isoformat()}) — "
        f"{scorecard.computed_total}"
    )

    body = _build_email_body(scorecard)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config.email
    msg["To"] = smtp_config.notify_email
    msg.attach(MIMEText(body, "plain"))

    logger.info("Sending scorecard email to %s", smtp_config.notify_email)
    with smtplib.SMTP(smtp_config.host, smtp_config.port) as server:
        server.starttls()
        server.login(smtp_config.email, smtp_config.password)
        server.send_message(msg)

    logger.info("Scorecard email sent successfully")


def _build_email_body(scorecard: Scorecard) -> str:
    lines = [
        "New golf round detected on Garmin Connect!",
        "",
        scorecard.summary(),
        "",
        "---",
        f"Garmin Activity ID: {scorecard.garmin_activity_id}",
        "",
        "If anything looks wrong, reply to this email with corrections.",
        "This round has NOT been posted to GHIN yet.",
    ]
    return "\n".join(lines)
