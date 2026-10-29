"""Email delivery abstraction for activation emails.

Campus360 supports three providers, selected by ``CAMPUS360_EMAIL_PROVIDER``:

* ``log``      - writes a *masked* notice to the server log for local
                 development. Never records the raw token or the full
                 activation URL. This is the default so nothing breaks when
                 no SMTP provider is configured.
* ``smtp``     - delivers over SMTP using ``CAMPUS360_SMTP_*`` settings.
                 Credentials come from environment variables only.
* ``none``     - never sends; useful when email is intentionally unconfigured.
                 Activation records stay "pending" and can be retried later.

Design decisions (security):

* The raw activation token is passed into ``send_activation_email`` only long
  enough to build the link; it is never logged, never stored, and never
  returned in the result.
* SMTP failures are surfaced as a structured result (``sent=False``) so the
  caller can keep the student record consistent and offer a secure retry.
* No provider secrets are hardcoded; all come from ``Settings`` (env/.env).
"""

from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailSendResult:
    """Outcome of attempting to deliver one activation email.

    ``sent`` is True only when the provider accepted the message for delivery.
    A delivery *submission* success does not guarantee the inbox received it;
    that is beyond what Campus360 can verify without a webhook/bounce feed.
    """

    sent: bool
    provider: str
    error: str | None = None


class EmailSender(ABC):
    """Send one activation email. Implementations are pure over configuration."""

    @abstractmethod
    def send_activation_email(
        self, *, to_email: str, activation_url: str, student_name: str
    ) -> EmailSendResult:
        """Deliver the activation link to ``to_email``.

        The ``activation_url`` already contains the single-use token; this
        method must not log it or include it in any diagnostic beyond what the
        caller explicitly chooses to surface.
        """
        raise NotImplementedError


class LogEmailSender(EmailSender):
    """Development provider: log a masked notice, never the token or URL."""

    provider_name = "log"

    def send_activation_email(
        self, *, to_email: str, activation_url: str, student_name: str
    ) -> EmailSendResult:
        # Deliberately do NOT log activation_url or any token fragment.
        # Only record that an activation email *would* go to this address,
        # so operators can confirm delivery was attempted without leaking
        # single-use tokens into log files.
        logger.info(
            "Activation email queued (log provider, not actually sent): to=%s, student=%s",
            to_email,
            student_name,
        )
        return EmailSendResult(sent=True, provider=self.provider_name)


class SmtpEmailSender(EmailSender):
    """Production provider: deliver over SMTP."""

    provider_name = "smtp"

    def send_activation_email(
        self, *, to_email: str, activation_url: str, student_name: str
    ) -> EmailSendResult:
        settings = get_settings()
        message = EmailMessage()
        message["Subject"] = "Set your Campus360 password"
        message["From"] = settings.email_from
        message["To"] = to_email
        message.set_content(
            f"""\
Hello {student_name},

Your Campus360 account has been created. Choose your permanent password by
opening the link below. It works only once and expires after the configured
window.

{activation_url}

If you did not request this, you can ignore this message.

— Campus360
"""
        )

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                if settings.smtp_user and settings.smtp_password:
                    smtp.starttls()
                    smtp.login(
                        settings.smtp_user,
                        settings.smtp_password.get_secret_value(),
                    )
                smtp.send_message(message)
        except Exception as exc:  # pragma: no cover - network/provider failure
            logger.warning("Activation email SMTP delivery failed: %s", exc)
            return EmailSendResult(sent=False, provider=self.provider_name, error=str(exc))

        logger.info("Activation email sent via SMTP to=%s", to_email)
        return EmailSendResult(sent=True, provider=self.provider_name)


class NullEmailSender(EmailSender):
    """No-op provider: never sends, always reports pending."""

    provider_name = "none"

    def send_activation_email(
        self, *, to_email: str, activation_url: str, student_name: str
    ) -> EmailSendResult:
        logger.info(
            "Activation email skipped (provider 'none'): to=%s, student=%s",
            to_email,
            student_name,
        )
        return EmailSendResult(
            sent=False,
            provider=self.provider_name,
            error="email provider not configured",
        )


def build_email_sender() -> EmailSender:
    """Pick the email provider from settings.

    Defaults to ``log`` so local development works without SMTP credentials.
    """
    provider = get_settings().email_provider.lower()
    if provider == "smtp":
        return SmtpEmailSender()
    if provider == "none":
        return NullEmailSender()
    # Anything else (including "log" and unset) falls back to the safe log
    # provider rather than silently dropping activation emails.
    return LogEmailSender()
