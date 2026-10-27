"""Transactional email (verification + password reset) over SMTP.

Works with any SMTP provider (Gmail app password, Resend, Brevo, Amazon SES...).
When SMTP is not configured, emails are skipped; in development the link is
logged so the flow can still be tested locally.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)


def email_configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_from)


def send_email(to: str, subject: str, text: str, html: str) -> bool:
    s = get_settings()
    if not email_configured():
        if s.app_env == "development":
            log.warning("SMTP not configured - email to %s not sent:\n%s", to, text)
        return False
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        if s.smtp_port == 465:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
            if s.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
        with server:
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        log.exception("failed to send email to %s", to)
        return False


def _layout(title: str, body: str, link: str, button: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#f6f4ec;font-family:Inter,Segoe UI,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table width="480" cellpadding="0" cellspacing="0" style="max-width:480px;background:#fffefa;border-radius:16px;border:1px solid #e4dfd0">
<tr><td style="padding:28px 28px 8px;font-size:22px;font-weight:700;color:#1f7a4d">🥗 NutriTrack</td></tr>
<tr><td style="padding:8px 28px;font-size:18px;font-weight:600;color:#0b0b0b">{title}</td></tr>
<tr><td style="padding:4px 28px 20px;font-size:15px;line-height:1.5;color:#52514e">{body}</td></tr>
<tr><td style="padding:0 28px 28px"><a href="{link}" style="display:inline-block;background:#1f7a4d;color:#fff;
text-decoration:none;font-weight:600;padding:12px 20px;border-radius:10px">{button}</a></td></tr>
<tr><td style="padding:0 28px 24px;font-size:12px;color:#75746e">If the button doesn't work, open this link:<br>{link}</td></tr>
</table></td></tr></table></body></html>"""


def send_verification(to: str, link: str) -> bool:
    return send_email(
        to,
        "Confirm your NutriTrack email",
        f"Welcome to NutriTrack! Confirm your email address:\n{link}\n\nThis link expires in 48 hours.",
        _layout("Confirm your email", "Welcome! Tap below to confirm this is your email address. "
                "The link expires in 48 hours.", link, "Confirm email"),
    )


def send_password_reset(to: str, link: str) -> bool:
    return send_email(
        to,
        "Reset your NutriTrack password",
        f"Reset your NutriTrack password:\n{link}\n\nThis link expires in 1 hour. "
        "If you didn't ask for this, you can ignore this email.",
        _layout("Reset your password", "Someone (hopefully you) asked to reset your password. "
                "The link expires in 1 hour. If it wasn't you, ignore this email.", link, "Choose a new password"),
    )
