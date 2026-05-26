import smtplib
import os
import ssl
import html as _html_mod
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def _html_escape(s: str) -> str:
    """Escape user-provided text before interpolating into an HTML email body."""
    return _html_mod.escape(s or "", quote=True)

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))
SMTP_USER = os.environ.get("SMTP_USER", "admin@example.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "password")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@sigmaai.ai")
APP_URL = os.environ.get("APP_URL", "https://sigmaai.ai/library")

def _send_email(to_email: str, subject: str, html_content: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email

    part = MIMEText(html_content, "html")
    msg.attach(part)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host=SMTP_SERVER, port=SMTP_PORT, context=context) as server:
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
    except Exception as e:
        print(f"Failed to send email to {to_email}. Error: {e}")
        # In a production app you might want to log this to a proper logger
        # and possibly raise an error depending on strictness.

def send_admin_notification(user_email: str, token: str, source: str = None, purpose: str = None):
    approve_link = f"{APP_URL}/api/admin/approve?token={token}"
    reject_link = f"{APP_URL}/api/admin/reject?token={token}"

    html = f"""
    <html>
      <body>
        <h3>New Registration Request for Urantia Library</h3>
        <p><strong>Email:</strong> {user_email}</p>
        <p><strong>Source:</strong> {source or 'Not provided'}</p>
        <p><strong>Purpose:</strong> {purpose or 'Not provided'}</p>
        <br>
        <p>
            <a href="{approve_link}" style="padding: 10px 15px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px;">Approve</a>
            &nbsp;&nbsp;&nbsp;
            <a href="{reject_link}" style="padding: 10px 15px; background-color: #dc3545; color: white; text-decoration: none; border-radius: 5px;">Reject</a>
        </p>
      </body>
    </html>
    """
    _send_email(ADMIN_EMAIL, "New Registration Request", html)

def send_user_approval(user_email: str, token: str, language: str = "en"):
    setup_link = f"{APP_URL}/#/set-password?token={token}"
    bodies = {
        "en": (
            "Registration Approved - Action Required",
            f"""
            <html>
              <body>
                <h3>Your request for Urantia Library has been approved!</h3>
                <p>Welcome! You can now finalize your registration by setting up your password.</p>
                <p><a href="{setup_link}">Click here to set your password and log in</a></p>
              </body>
            </html>
            """,
        ),
        "ru": (
            "Регистрация одобрена — требуется действие",
            f"""
            <html>
              <body>
                <h3>Ваш запрос в Урантийскую библиотеку одобрен!</h3>
                <p>Добро пожаловать! Чтобы завершить регистрацию, задайте пароль.</p>
                <p><a href="{setup_link}">Нажмите здесь, чтобы задать пароль и войти</a></p>
              </body>
            </html>
            """,
        ),
    }
    subject, html = bodies.get(language, bodies["en"])
    _send_email(user_email, subject, html)

def send_moderation_digest(pending_count: int):
    """Throttled digest sent to the admin when book comments await moderation."""
    link = f"{APP_URL}/#/admin/comments"
    noun = "comment" if pending_count == 1 else "comments"
    html = f"""
    <html>
      <body>
        <h3>Comments awaiting moderation — Urantia Library</h3>
        <p>There {'is' if pending_count == 1 else 'are'} <strong>{pending_count}</strong> {noun} waiting to be reviewed.</p>
        <p><a href="{link}" style="padding: 10px 15px; background-color: #2563eb; color: white; text-decoration: none; border-radius: 5px;">Open moderation queue</a></p>
      </body>
    </html>
    """
    _send_email(ADMIN_EMAIL, "Comments awaiting moderation", html)

def send_user_rejection(user_email: str, language: str = "en"):
    bodies = {
        "en": (
            "Registration Update",
            """
            <html>
              <body>
                <h3>Update on your Urantia Library registration</h3>
                <p>Thank you for your interest. Unfortunately, we are unable to grant you access at this time.</p>
              </body>
            </html>
            """,
        ),
        "ru": (
            "Обновление по вашей регистрации",
            """
            <html>
              <body>
                <h3>Обновление по вашей регистрации в Урантийской библиотеке</h3>
                <p>Спасибо за интерес к проекту. К сожалению, мы не можем предоставить вам доступ в данный момент.</p>
              </body>
            </html>
            """,
        ),
    }
    subject, html = bodies.get(language, bodies["en"])
    _send_email(user_email, subject, html)


# ==============================================================================
# Feedback / contact-admin email helpers
# ==============================================================================

def _resolve_admin_recipients() -> list[str]:
    """Combine ADMIN_EMAIL env + admin_feedback_settings.extra_recipients into
    a de-duped list (order preserved). Caller-side: opens its own DB session
    so the digest can run from a background thread."""
    import models                                # local import to avoid cycles
    from database import SessionLocal
    db = SessionLocal()
    try:
        s = db.query(models.AdminFeedbackSettings).filter(
            models.AdminFeedbackSettings.id == 1
        ).first()
        extras = [e.strip() for e in (s.extra_recipients or "").split(",") if e.strip()] if s else []
    finally:
        db.close()
    recipients = [ADMIN_EMAIL] + extras
    seen: set[str] = set()
    return [r for r in recipients if not (r in seen or seen.add(r))]


def _format_category(brief: dict) -> str:
    cat = (brief.get("category") or "").title()
    if brief.get("category") == "book" and brief.get("book_subcategory"):
        return f"Book · {brief['book_subcategory']}"
    return cat


def _digest_rows_html(briefs: list[dict]) -> str:
    rows = ""
    for b in briefs:
        rows += (
            "<tr style=\"border-top: 1px solid #e5e7eb;\">"
            f"<td style=\"padding: 10px; color: #111827; font-family: ui-monospace, monospace;\">{_html_escape(b['public_id'])}</td>"
            f"<td style=\"padding: 10px; color: #6b7280; white-space: nowrap;\">{_html_escape(_format_category(b))}</td>"
            f"<td style=\"padding: 10px; color: #111827;\">{_html_escape(b['subject'])}</td>"
            "</tr>"
        )
    return rows


def _digest_plain(briefs: list[dict]) -> str:
    lines = []
    for b in briefs:
        lines.append(f"{b['public_id']} · {_format_category(b)} · {b['subject']}")
    return "\n".join(lines)


def _send_email_multipart(to_email: str, subject: str, html_content: str, plain_content: str) -> None:
    """Like _send_email but with a text/plain alternative for deliverability."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg.attach(MIMEText(plain_content, "plain"))
    msg.attach(MIMEText(html_content, "html"))
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host=SMTP_SERVER, port=SMTP_PORT, context=context) as server:
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
    except Exception as e:
        print(f"Failed to send email to {to_email}. Error: {e}")


def _send_one_digest(to_email: str, briefs: list[dict]) -> None:
    """Render and send a single per-recipient feedback digest."""
    if not briefs:
        return
    link = f"{APP_URL}/#/admin/feedback"
    count = len(briefs)
    noun = "feedback message" if count == 1 else "feedback messages"
    rows = _digest_rows_html(briefs)
    subject = f"{count} new {noun} — Urantia Library"
    html = f"""
    <html>
      <body style="font-family: Arial, Helvetica, sans-serif; color: #1f2937;">
        <p><strong>{count} new {noun}</strong> are waiting on the admin inbox since the last digest.</p>
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px;">
          <thead>
            <tr style="background: #f3f4f6; color: #374151;">
              <th align="left" style="padding: 8px 10px;">Ticket</th>
              <th align="left" style="padding: 8px 10px;">Category</th>
              <th align="left" style="padding: 8px 10px;">Subject</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <p><a href="{link}" style="padding: 10px 16px; background: #2563eb; color: #fff; text-decoration: none; border-radius: 5px; font-weight: 600;">Open feedback inbox</a></p>
        <p style="font-size: 13px; color: #6b7280;">We send this digest at most once every interval while messages are waiting — never more often, even if a flood arrives.</p>
      </body>
    </html>
    """
    plain = f"{count} new {noun} waiting.\n\n" + _digest_plain(briefs) + f"\n\nOpen inbox: {link}\n"
    _send_email_multipart(to_email, subject, html, plain)


def send_feedback_digest_per_recipient(per_recipient: dict[str, list[dict]]) -> None:
    """Fire one digest per admin email, each containing only the threads they
    should see. Caller (_maybe_send_feedback_digest) builds the partition map."""
    for to_email, briefs in per_recipient.items():
        _send_one_digest(to_email, briefs)


def send_feedback_digest(briefs: list[dict], recipients: list[str] | None = None) -> None:
    """Throttled digest of new feedback to the broadcast admin list. Used by
    paths that don't need per-recipient partitioning (force-send to all)."""
    if not briefs:
        return
    targets = recipients or _resolve_admin_recipients()
    for to_email in targets:
        _send_one_digest(to_email, briefs)


def send_feedback_user_update(
    user_email: str, public_id: str, subject: str, status: str, change: str,
) -> None:
    """Per-event email to the author. change ∈ {'reply', 'status'}."""
    link = f"{APP_URL}/#/feedback/{public_id}"
    status_label = {
        "new": "New", "open": "Open", "triage": "Triage", "progress": "In progress",
        "waiting": "Waiting on you", "resolved": "Resolved", "closed": "Closed",
        "archived": "Archived",
    }.get(status, status)
    status_color_bg = {
        "progress": "#ede9fe", "waiting": "#fef3c7", "resolved": "#d1fae5", "closed": "#e5e7eb",
    }.get(status, "#dbeafe")
    status_color_fg = {
        "progress": "#5b21b6", "waiting": "#92400e", "resolved": "#065f46", "closed": "#4b5563",
    }.get(status, "#1e40af")

    reply_row = (
        "<tr style=\"border-bottom: 1px solid #e5e7eb;\">"
        "<td style=\"padding: 10px; color: #6b7280;\">Admin reply</td>"
        "<td style=\"padding: 10px; color: #111827;\">The admin wrote a reply.</td>"
        "</tr>"
    ) if change == "reply" else ""

    status_row = (
        "<tr style=\"border-top: 1px solid #e5e7eb; border-bottom: 1px solid #e5e7eb;\">"
        "<td style=\"padding: 10px; color: #6b7280; width: 110px;\">Status</td>"
        "<td style=\"padding: 10px; color: #111827;\">"
        f"<span style=\"background: {status_color_bg}; color: {status_color_fg}; padding: 2px 8px; border-radius: 4px; font-weight: 600;\">{_html_escape(status_label)}</span>"
        "</td></tr>"
    )

    html = f"""
    <html>
      <body style="font-family: Arial, Helvetica, sans-serif; color: #1f2937;">
        <p>Hi,</p>
        <p>There's an update on the feedback you sent: <strong>"{_html_escape(subject)}"</strong>.</p>
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px;">
          <tbody>{status_row}{reply_row}
            <tr style="border-bottom: 1px solid #e5e7eb;">
              <td style="padding: 10px; color: #6b7280;">Ticket</td>
              <td style="padding: 10px; color: #111827; font-family: ui-monospace, monospace;">{_html_escape(public_id)}</td>
            </tr>
          </tbody>
        </table>
        <p><a href="{link}" style="padding: 10px 16px; background: #2563eb; color: #fff; text-decoration: none; border-radius: 5px; font-weight: 600;">View the discussion</a></p>
        <p style="font-size: 13px; color: #6b7280;">Reply through the site so the thread stays in one place — emailing back to this address won't reach the admin.</p>
        <p style="font-size: 11px; color: #9ca3af;">You're receiving this because you sent feedback to Urantia Library. <a href="{APP_URL}/#/settings?tab=Notifications" style="color: #2563eb;">Manage email notifications</a>.</p>
      </body>
    </html>
    """
    plain = (
        f"Update on your feedback: {subject}\n"
        f"Status: {status_label}\n"
        f"Ticket: {public_id}\n"
        + ("The admin wrote a reply.\n" if change == "reply" else "")
        + f"\nView: {link}\n"
    )
    _send_email_multipart(user_email, f"Update on your feedback · {subject}", html, plain)


def send_feedback_urgent(
    public_id: str, subject: str, book_subcategory: str, recipients: list[str],
) -> None:
    """Single-item email for urgent reports (copyright / inappropriate). Bypasses
    the throttle. Recipients resolved per-thread by the caller."""
    if not recipients:
        return
    link = f"{APP_URL}/#/admin/feedback"
    brief = {
        "public_id": public_id, "subject": subject,
        "category": "book", "book_subcategory": book_subcategory,
    }
    rows = _digest_rows_html([brief])
    subj = f"URGENT · {_format_category(brief)} · {subject}"
    html = f"""
    <html>
      <body style="font-family: Arial, Helvetica, sans-serif; color: #1f2937;">
        <div style="background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; padding: 10px 14px; border-radius: 6px; font-weight: 600;">
          URGENT — a {_html_escape(book_subcategory)} report was just filed.
        </div>
        <p style="margin-top: 16px;">This message bypasses the regular digest so you can act on it immediately.</p>
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px;">
          <thead>
            <tr style="background: #f3f4f6; color: #374151;">
              <th align="left" style="padding: 8px 10px;">Ticket</th>
              <th align="left" style="padding: 8px 10px;">Category</th>
              <th align="left" style="padding: 8px 10px;">Subject</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <p><a href="{link}" style="padding: 10px 16px; background: #dc2626; color: #fff; text-decoration: none; border-radius: 5px; font-weight: 600;">Open feedback inbox</a></p>
      </body>
    </html>
    """
    plain = (
        f"URGENT — {book_subcategory} report\n"
        + _digest_plain([brief])
        + f"\n\nOpen inbox: {link}\n"
    )
    for to_email in recipients:
        _send_email_multipart(to_email, subj, html, plain)
