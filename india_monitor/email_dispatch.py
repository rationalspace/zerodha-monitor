"""Gmail SMTP dispatcher for India alerts."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config_loader import EmailConfig
from .rules.sell_near_high import Alert
from .secrets import get_secret

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


class EmailDispatcher:
    def __init__(self, cfg: EmailConfig) -> None:
        self.cfg = cfg
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def send_alert(self, alert: Alert, *, dry_run: bool = False) -> None:
        template = self.env.get_template("india_alert.html.j2")
        html = template.render(alert=alert, p=alert.payload)
        subject = f"[INDIA] {alert.title}"
        self._send(subject=subject, html=html, dry_run=dry_run)

    def _send(self, *, subject: str, html: str, dry_run: bool) -> None:
        from_address = self.cfg.from_address or get_secret("gmail_address")
        to_address = self.cfg.to_address

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_address
        msg["To"] = to_address
        msg.set_content("This email requires an HTML-capable client.")
        msg.add_alternative(html, subtype="html")

        if dry_run:
            log.info("[DRY-RUN] Would send: %r → %s", subject, to_address)
            return

        password = get_secret("gmail_app_password")
        with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port) as server:
            server.starttls()
            server.login(from_address, password)
            server.send_message(msg)
        log.info("Sent: %r", subject)
