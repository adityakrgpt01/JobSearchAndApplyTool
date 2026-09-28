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
        poll_interval: int = 4,
        received_after_ts: Optional[float] = None
    ) -> Optional[str]:
        """
        Polls Gmail inbox over SSL for a verification email matching sender_keyword
        and extracts the verification PIN received after received_after_ts.
        """
        if not self.app_password:
            print("[GmailReader] Note: GMAIL_APP_PASSWORD not set. Waiting for user setup.")
            return None

        # Default received_after_ts to 30s before now if not provided
        if received_after_ts is None:
            received_after_ts = time.time() - 30.0

        print(f"[GmailReader] Connecting to imap.gmail.com as {self.user} (Watching for '{sender_keyword}' email received after {int(received_after_ts)})...")
        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            try:
                mail = imaplib.IMAP4_SSL("imap.gmail.com")
                mail.login(self.user, self.app_password)
                mail.select("inbox")

                # Search latest messages directly in reverse chronological order
                status, messages = mail.search(None, "ALL")

                if status == "OK" and messages[0]:
                    msg_ids = messages[0].split()
                    # Inspect last 10 messages in reverse chronological order
                    for msg_id in reversed(msg_ids[-10:]):
                        res, msg_data = mail.fetch(msg_id, "(RFC822)")
                        if res != "OK":
                            continue

                        raw_email = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        
                        # Check email date if available
                        date_str = msg.get("Date", "")
                        if date_str:
                            try:
                                import email.utils
                                msg_dt = email.utils.parsedate_to_datetime(date_str)
                                if msg_dt and msg_dt.timestamp() < (received_after_ts - 5.0):
                                    # Message was received before this submission started
                                    continue
                            except Exception:
                                pass

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
                                print(f"[GmailReader] ✅ Extracted FRESH Verification PIN: {code} from subject: '{subject}'")
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
                content_disposition = str(part.get("Content-Disposition") or "")
                if content_type in ("text/plain", "text/html") and "attachment" not in content_disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body += payload.decode("utf-8", errors="ignore") + " "
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode("utf-8", errors="ignore")
        return body

    def _extract_pin(self, text: str) -> Optional[str]:
        """Extracts verification code from email text (supports 6-digit PINs and 8-character Greenhouse security codes)."""
        # 1. Look for Greenhouse / Portal 8-char security code: e.g. <h1>5BauOzP1</h1> or code into the security code field: 5BauOzP1
        m_gh = re.search(r'(?:<h\d>|\bcode(?:\s+is|\s+into|\s*[:=-])?\s*)([A-Za-z0-9]{8})(?:</h\d>|\b|\s)', text, re.IGNORECASE)
        if m_gh:
            candidate = m_gh.group(1).strip()
            # Verify candidate has both letters and numbers or is 8 chars
            if len(candidate) == 8 and not candidate.lower() in ("security", "recruiti"):
                return candidate

        # 2. Look for explicit 6-digit or 4-8 digit numeric code
        m = re.search(r'(?:code|pin|verification|passcode)\D{1,15}(\b\d{6}\b)', text, re.IGNORECASE)
        if m:
            return m.group(1)
        
        # 3. Fallback: any isolated 6-digit block
        m2 = re.search(r'\b\d{6}\b', text)
        if m2:
            return m2.group(0)

        # 4. Fallback 8-character alphanumeric tag
        m3 = re.search(r'<h\d>([A-Za-z0-9]{6,10})</h\d>', text)
        if m3:
            return m3.group(1).strip()

        return None

if __name__ == "__main__":
    reader = GmailVerificationReader()
    print("Gmail Verification Reader initialized.")
