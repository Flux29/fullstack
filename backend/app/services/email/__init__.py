"""Email module — transactional email via Resend, SMTP, or log (dev)."""

import logging

from app.core.config import settings
from app.services.email.providers.base import EmailProvider

logger = logging.getLogger(__name__)


def get_email_provider() -> EmailProvider:
    match settings.EMAIL_PROVIDER:
        case "resend":
            if settings.RESEND_API_KEY:
                from app.services.email.providers.resend import ResendProvider

                return ResendProvider(api_key=settings.RESEND_API_KEY)
            logger.warning(
                "EMAIL_PROVIDER=resend but RESEND_API_KEY is not set; falling back to the log provider"
            )
        case "smtp":
            if settings.SMTP_HOST:
                from app.services.email.providers.smtp import SMTPProvider

                return SMTPProvider(
                    host=settings.SMTP_HOST,
                    port=settings.SMTP_PORT,
                    username=settings.SMTP_USERNAME,
                    password=settings.SMTP_PASSWORD,
                    use_tls=settings.SMTP_USE_TLS,
                )
            logger.warning(
                "EMAIL_PROVIDER=smtp but SMTP_HOST is not set; falling back to the log provider"
            )
        case "log":
            pass
        case unknown:
            logger.warning("Unknown EMAIL_PROVIDER %r; falling back to the log provider", unknown)

    from app.services.email.providers.log import LogProvider

    return LogProvider(write_to_disk=settings.LOG_PROVIDER_WRITE_TO_DISK)
