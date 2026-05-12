"""Outbound email via Mandrill SMTP.

Mandrill (Mailchimp Transactional) accepts any-username on STARTTLS at
smtp.mandrillapp.com:587 — the API key is the password. Same SMTP path
the orchestration host's status-dashboard uses.

Why SMTP and not the Mailchimp Transactional REST API? Two reasons:
  - We already have working SMTP creds across the org.
  - stdlib smtplib has no extra dependency.

For dev/test, set MAIL_DEV_MODE=true to print the email to stdout instead
of sending — keeps test runs from burning Mandrill quota.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger(__name__)


def send_email(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> None:
    """Send one transactional email. Raises on SMTP failure (caller decides
    whether to surface or swallow)."""

    from_addr = settings.smtp_from or settings.smtp_username
    if not from_addr:
        raise RuntimeError("smtp_from / smtp_username not configured")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    if settings.mail_dev_mode:
        log.warning(
            "MAIL_DEV_MODE — not sending. To=%s Subject=%s\n%s",
            to, subject, text_body,
        )
        return

    if not (settings.smtp_host and settings.smtp_username and settings.smtp_password):
        raise RuntimeError("SMTP creds incomplete; check SMTP_* in .env")

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
        s.ehlo()
        s.starttls(context=context)
        s.ehlo()
        s.login(settings.smtp_username, settings.smtp_password)
        s.send_message(msg)
    log.info("sent email to=%s subject=%r", to, subject)


def send_magic_link(*, to: str, link: str, expires_minutes: int = 15) -> None:
    """Render + send the sign-in email."""
    subject = "Sign in to ProspectForge"
    text_body = (
        "Hello,\n\n"
        "Click the link below to sign in to ProspectForge:\n\n"
        f"{link}\n\n"
        f"This link expires in {expires_minutes} minutes and can be used once.\n\n"
        "If you didn't request this, ignore this email — nothing happens until "
        "the link is clicked.\n\n"
        "— ProspectForge\n"
    )
    html_body = f"""\
<!doctype html>
<html><body style="font-family:'Work Sans',system-ui,sans-serif;color:#003366;max-width:580px;margin:24px auto;padding:24px;background:#f7f7f7;">
  <h2 style="font-weight:700;margin-top:0;">Sign in to ProspectForge</h2>
  <p>Click the button below to sign in. This link expires in {expires_minutes} minutes and can be used once.</p>
  <p style="margin:24px 0;">
    <a href="{link}" style="display:inline-block;background:#2d8049;color:#ffffff;padding:10px 20px;border-radius:4px;text-decoration:none;font-weight:700;">Sign in</a>
  </p>
  <p style="color:#555;font-size:13px;">If the button doesn't work, paste this URL into your browser:<br>
    <span style="font-family:ui-monospace,Menlo,monospace;font-size:12px;word-break:break-all;">{link}</span>
  </p>
  <hr style="border:none;border-top:1px solid #ddd;margin:24px 0;">
  <p style="color:#777;font-size:12px;">If you didn't request this, ignore this email — nothing happens until the link is clicked.</p>
</body></html>
"""
    send_email(to=to, subject=subject, text_body=text_body, html_body=html_body)
