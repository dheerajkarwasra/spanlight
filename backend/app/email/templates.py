"""The transactional emails: verify an address, reset a password, change the address, accept an
invite.

The copy is fixed here, not in a template engine. The plain-text part carries values raw, since
it is not markup. The HTML part renders the same paragraphs and escapes every value in them.
Templates return the message with `to=""`; the caller fills the recipient with
`dataclasses.replace`.
"""

from html import escape

from app.email.message import EmailMessage

# A paragraph is copy as is, or `(lead_in, url)`: the URL sits on its own line under the lead-in.
_Paragraph = str | tuple[str, str]


def render_verify_email(name: str, url: str) -> EmailMessage:
    """Confirm an address for a new account. The link works once and expires in 24 hours."""
    return _compose(
        subject="Verify your email for Spanlight",
        paragraphs=[
            f"Hi {name},",
            ("Confirm that this is your email address for Spanlight:", url),
            "The link works once and expires in 24 hours. "
            "If you didn't create a Spanlight account, you can ignore this email.",
        ],
    )


def render_password_reset(name: str, url: str) -> EmailMessage:
    """Start a password reset. The link works once and expires in 1 hour."""
    return _compose(
        subject="Reset your Spanlight password",
        paragraphs=[
            f"Hi {name},",
            (
                "Someone asked to reset the password for your Spanlight account. "
                "If that was you, choose a new password here:",
                url,
            ),
            "The link works once and expires in 1 hour. Resetting your password signs you out "
            "on every device. If you didn't ask for this, ignore this email; "
            "your password stays the same.",
        ],
    )


def render_email_change_confirm(name: str, url: str) -> EmailMessage:
    """Sent to the new address: confirm the change. The link works once and expires in 1 hour."""
    return _compose(
        subject="Confirm your new email for Spanlight",
        paragraphs=[
            f"Hi {name},",
            ("Confirm that this is the new email address for your Spanlight account:", url),
            "The link works once and expires in 1 hour. Changing your email signs you out on "
            "every device. If you didn't ask for this, ignore this email; nothing changes.",
        ],
    )


def render_email_change_notice(name: str) -> EmailMessage:
    """Sent to the current address: a change was asked for. It carries no link."""
    return _compose(
        subject="Your Spanlight email address is changing",
        paragraphs=[
            f"Hi {name},",
            "Someone asked to change the email address of your Spanlight account. We sent a "
            "confirmation link to the new address; the change happens only if that link is "
            "opened within 1 hour.",
            'If this wasn\'t you, reset your password now with "Forgot password" on the sign-in '
            "page. Resetting your password cancels the pending change.",
        ],
    )


def render_oauth_link_notice(name: str, provider_label: str) -> EmailMessage:
    """Tell the owner a sign-in provider was connected, without including a link."""
    return _compose(
        subject=f"{provider_label} was connected to your Spanlight account",
        paragraphs=[
            f"Hi {name},",
            f"A {provider_label} account was connected to your Spanlight account, and can now "
            "be used to sign in to it.",
            "If this wasn't you, sign in to Spanlight, disconnect it under Settings, Security, "
            "and reset your password.",
        ],
    )


def render_invite(org_name: str, role: str, url: str, inviter_name: str) -> EmailMessage:
    """Invite someone to an org with a role. The invite expires in 7 days."""
    return _compose(
        subject=f"{inviter_name} invited you to {org_name} on Spanlight",
        paragraphs=[
            f"{inviter_name} invited you to join {org_name} on Spanlight as {role}.",
            ("Accept the invite:", url),
            "The invite expires in 7 days. If you weren't expecting it, you can ignore this email.",
        ],
    )


def render_member_joined_notice(
    org_name: str, member_name: str, role: str, inviter_name: str
) -> EmailMessage:
    return _compose(
        subject=f"A member joined {org_name} on Spanlight",
        paragraphs=[
            f"{member_name} joined {org_name} as {role}.",
            f"They were invited by {inviter_name}.",
            "If you weren't expecting this, review your organization's members in Spanlight.",
        ],
    )


def render_audit_stream_notice(org_name: str, actor_name: str, change: str) -> EmailMessage:
    return _compose(
        subject=f"Audit streaming changed for {org_name} on Spanlight",
        paragraphs=[
            f"{actor_name} changed audit streaming for {org_name}: {change}.",
            "If you weren't expecting this, open Spanlight, review the audit stream and your "
            "organization's owners, and secure the affected account by resetting its password "
            "and signing out its other sessions.",
        ],
    )


def render_member_removed_notice(
    org_name: str, member: str, removed_by: str | None
) -> EmailMessage:
    return _compose(
        subject=(
            f"A member left {org_name} on Spanlight"
            if removed_by is None
            else f"A member was removed from {org_name} on Spanlight"
        ),
        paragraphs=[
            f"{member} left {org_name}."
            if removed_by is None
            else f"{member} was removed from {org_name} by {removed_by}.",
            "If you weren't expecting this, open Spanlight, review the audit stream and your "
            "organization's owners, and secure the affected account by resetting its password "
            "and signing out its other sessions.",
        ],
    )


def render_org_deleted_notice(org_name: str, actor: str) -> EmailMessage:
    return _compose(
        subject=f"{org_name} was deleted on Spanlight",
        paragraphs=[
            f"{actor} deleted {org_name} on Spanlight, with its projects, traces, members "
            "and audit log.",
            "If you weren't expecting this, secure the affected account by resetting its "
            "password and signing out its other sessions, and contact the person named above.",
        ],
    )


def render_project_deleted_notice(org_name: str, project_name: str, actor: str) -> EmailMessage:
    return _compose(
        subject=f"{project_name} in {org_name} was deleted on Spanlight",
        paragraphs=[
            f"{actor} deleted the project {project_name} in {org_name} on Spanlight, "
            "with its traces and API keys.",
            "If you weren't expecting this, secure the affected account by resetting its "
            "password and signing out its other sessions, and contact the person named above.",
        ],
    )


def _compose(subject: str, paragraphs: list[_Paragraph]) -> EmailMessage:
    text_parts: list[str] = []
    html_parts: list[str] = []
    for paragraph in paragraphs:
        if isinstance(paragraph, tuple):
            lead_in, url = paragraph
            text_parts.append(f"{lead_in}\n{url}")
            html_parts.append(
                f"<p>{escape(lead_in, quote=False)}<br>\n"
                f'<a href="{escape(url, quote=True)}">{escape(url, quote=True)}</a></p>'
            )
        else:
            text_parts.append(paragraph)
            html_parts.append(f"<p>{escape(paragraph, quote=False)}</p>")

    text = "\n\n".join(text_parts) + "\n"
    html = "<html><body>\n" + "\n".join(html_parts) + "\n</body></html>"
    return EmailMessage(to="", subject=subject, text=text, html=html)
