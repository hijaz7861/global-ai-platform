"""Minimal SMTP delivery for security-sensitive account emails."""
import smtplib
from email.message import EmailMessage
from app.core.config import settings

def send_password_reset_email(recipient: str, token: str) -> None:
    """Send a reset token without logging it or exposing SMTP errors to API clients."""
    if not settings.smtp_host or not settings.smtp_from_email:
        raise RuntimeError("SMTP_HOST and SMTP_FROM_EMAIL must be configured")
    message = EmailMessage()
    message["Subject"] = "Reset your Global AI Platform password"
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message.set_content("A password reset was requested for your Global AI Platform account.\n\n" + f"Reset token: {token}\n\n" + "Submit this token and your new password to POST /api/v1/auth/password-reset/confirm.\nThe token expires soon and can only be used once. If you did not request this, ignore this email.")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        server.ehlo()
        if settings.smtp_starttls:
            server.starttls()
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password or "")
        server.send_message(message)


def send_email_verification(recipient: str, token: str) -> None:
    """Send a single-use email ownership token without logging its value."""
    if not settings.smtp_host or not settings.smtp_from_email:
        raise RuntimeError("SMTP_HOST and SMTP_FROM_EMAIL must be configured")
    message = EmailMessage()
    message["Subject"] = "Verify your Global AI Platform email"
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message.set_content(
        "Verify ownership of your Global AI Platform email address.\n\n"
        f"Verification token: {token}\n\n"
        "Submit this token to POST /api/v1/auth/email-verification/confirm. "
        "The token expires soon and can only be used once. If you did not create this account, ignore this email."
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        server.ehlo()
        if settings.smtp_starttls:
            server.starttls()
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password or "")
        server.send_message(message)



def send_membership_invitation(recipient: str, token: str, organization_name: str) -> None:
    """Deliver an invitation token without logging it or exposing SMTP errors to API clients."""
    if not settings.smtp_host or not settings.smtp_from_email:
        raise RuntimeError("SMTP_HOST and SMTP_FROM_EMAIL must be configured")
    message = EmailMessage()
    message["Subject"] = f"Invitation to join {organization_name} on Global AI Platform"
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message.set_content(
        f"You have been invited to join {organization_name} on Global AI Platform.\n\n"
        f"Invitation token: {token}\n\n"
        "Sign in to the account matching this email address and submit the token to "
        "POST /api/v1/invitations/accept. The token expires soon and can only be used once. "
        "If you did not expect this invitation, ignore this email."
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        server.ehlo()
        if settings.smtp_starttls:
            server.starttls()
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password or "")
        server.send_message(message)
