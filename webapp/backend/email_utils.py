import smtplib
import os
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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

def send_user_approval(user_email: str, token: str):
    setup_link = f"{APP_URL}/#/set-password?token={token}"
    html = f"""
    <html>
      <body>
        <h3>Your request for Urantia Library has been approved!</h3>
        <p>Welcome! You can now finalize your registration by setting up your password.</p>
        <p><a href="{setup_link}">Click here to set your password and log in</a></p>
      </body>
    </html>
    """
    _send_email(user_email, "Registration Approved - Action Required", html)

def send_user_rejection(user_email: str):
    html = f"""
    <html>
      <body>
        <h3>Update on your Urantia Library registration</h3>
        <p>Thank you for your interest. Unfortunately, we are unable to grant you access at this time.</p>
      </body>
    </html>
    """
    _send_email(user_email, "Registration Update", html)
