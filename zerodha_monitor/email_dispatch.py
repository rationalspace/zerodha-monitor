"""Gmail SMTP dispatcher for India alerts."""

from __future__ import annotations

import logging
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config_loader import EmailConfig
from .rules.sell_near_high import Alert
from .secrets import get_secret

if TYPE_CHECKING:
    pass

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

    def send_digest(self, alerts: list[Alert], data_date: date, *, dry_run: bool = False) -> None:
        """Send one digest email covering all alerts for data_date, sorted as passed in."""
        profitable = [a for a in alerts if (a.payload.get("unrealized_pl") or 0) >= 0]
        losses     = [a for a in alerts if (a.payload.get("unrealized_pl") or 0) < 0]

        # Build short ticker list for subject line
        exit_now   = [a.symbol for a in alerts if a.payload.get("exit_tier") == "exit_now"]
        exit_soon  = [a.symbol for a in alerts if a.payload.get("exit_tier") == "exit_soon"]
        others     = [a.symbol for a in alerts
                      if a.payload.get("exit_tier") not in ("exit_now", "exit_soon")]
        parts = []
        if exit_now:
            parts.append(f"EXIT NOW: {', '.join(exit_now[:4])}")
        if exit_soon:
            parts.append(f"soon: {', '.join(exit_soon[:3])}")
        if others:
            parts.append(f"rally: {', '.join(others[:3])}")
        summary = " | ".join(parts) if parts else f"{len(alerts)} alerts"

        date_str = data_date.strftime("%b %d")
        subject  = f"[INDIA {date_str}] {len(alerts)} alerts — {summary}"

        template = self.env.get_template("india_digest.html.j2")
        html = template.render(
            data_date=data_date,
            date_str=date_str,
            alerts=alerts,
            profitable=profitable,
            losses=losses,
            total=len(alerts),
        )
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
