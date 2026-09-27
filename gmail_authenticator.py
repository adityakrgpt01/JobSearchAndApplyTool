"""
Gmail IMAP Verification Code Reader & Account Authenticator.
Connects securely via IMAP over SSL to fetch 6-digit verification codes
sent by Workday and other employer career portals.
"""

import os
import re
import time
import email
import imaplib
from email.header import decode_header
from typing import Optional

def _load_env_file():
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip()

_load_env_file()

GMAIL_USER = os.getenv("GMAIL_USER", "adityakumargupta521@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

class GmailVerificationReader:
    def __init__(self, user: str = GMAIL_USER, app_password: str = GMAIL_APP_PASSWORD):
        self.user = user
        self.app_password = app_password.replace(" ", "")

    def fetch_latest_verification_code(
        self,
        sender_keyword: str = "workday",
        timeout_seconds: int = 60,
        poll_interval: int = 4
    ) -> Optional[str]:
        """
        Polls Gmail inbox over SSL for a verification email matching sender_keyword
        and extracts the 6-digit verification PIN.
        """
        if not self.app_password:
            print("[GmailReader] Note: GMAIL_APP_PASSWORD not set. Waiting for user setup.")
            return None

        print(f"[GmailReader] Connecting to imap.gmail.com as {self.user} (Watching for '{sender_keyword}' email)...")
        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            try:
                mail = imaplib.IMAP4_SSL("imap.gmail.com")
                mail.login(self.user, self.app_password)
                mail.select("inbox")

                # Search unread / recent messages
                status, messages = mail.search(None, "UNSEEN")
                if status != "OK" or not messages[0]:
                    # Also search latest messages
                    status, messages = mail.search(None, "ALL")

                if status == "OK" and messages[0]:
                    msg_ids = messages[0].split()
                    # Inspect last 5 messages in reverse chronological order
                    for msg_id in reversed(msg_ids[-5:]):
                        res, msg_data = mail.fetch(msg_id, "(RFC822)")
                        if res != "OK":
                            continue

                        raw_email = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        
                        subject, encoding = decode_header(msg.get("Subject", ""))[0]
                        if isinstance(subject, bytes):
                            subject = subject.decode(encoding or "utf-8", errors="ignore")
                        from_header = msg.get("From", "")

                        # Check if message matches Workday / portal
                        is_target = sender_keyword.lower() in from_header.lower() or sender_keyword.lower() in subject.lower() or "verification" in subject.lower() or "code" in subject.lower() or "security code" in subject.lower()

                        if is_target:
                            body_text = self._extract_body(msg)
                            code = self._extract_pin(subject + " " + body_text)
                            if code:
                                print(f"[GmailReader] ✅ Extracted Verification PIN: {code} from subject: '{subject}'")
                                mail.logout()
                                return code

                mail.logout()
            except Exception as e:
                print(f"[GmailReader] Poll warning: {e}")

            time.sleep(poll_interval)

        print("[GmailReader] Timed out waiting for verification email.")
        return None

    def _extract_body(self, msg) -> str:
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body += payload.decode("utf-8", errors="ignore") + " "
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode("utf-8", errors="ignore")
        return body

    def _extract_pin(self, text: str) -> Optional[str]:
        """Extracts 6-digit verification code from email text."""
        # Matches patterns like 'verification code is 123456' or 'code: 123456' or standalone 6-digit numbers
        m = re.search(r'(?:code|pin|verification|passcode)\D{1,15}(\b\d{6}\b)', text, re.IGNORECASE)
        if m:
            return m.group(1)
        
        # Fallback: any isolated 6-digit block
        m2 = re.search(r'\b\d{6}\b', text)
        if m2:
            return m2.group(0)

        return None

if __name__ == "__main__":
    reader = GmailVerificationReader()
    print("Gmail Verification Reader initialized.")
