#!/usr/bin/env python3
"""Offline + Gmail + Playwright checks for Greenhouse email security-code handling."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gmail_authenticator import GmailVerificationReader
from greenhouse_email_verify import enter_security_code, find_security_inputs, handle_greenhouse_security_code


SAMPLES = {
    "h1": ("<html><h1>5BauOzP1</h1><p>Enter this security code</p></html>", "5BauOzP1"),
    "colon": ("Your security code is: Kq8mN2pR\nThanks,", "Kq8mN2pR"),
    "into": ("Please enter the code into the security code field: aB3dE6gH", "aB3dE6gH"),
    "workday6": ("Your verification code is 482917. It expires in 10 minutes.", "482917"),
}


def test_extractor() -> bool:
    reader = GmailVerificationReader()
    ok = True
    print("== PIN extractor ==")
    for name, (text, expected) in SAMPLES.items():
        got = reader._extract_pin(text)
        status = "PASS" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  [{status}] {name}: expected={expected} got={got}")
    return ok


def test_gmail_login() -> bool:
    print("== Gmail IMAP ==")
    reader = GmailVerificationReader()
    if not reader.app_password:
        print("  FAIL: GMAIL_APP_PASSWORD is not set")
        return False
    import email as email_mod
    import imaplib
    from email.header import decode_header

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(reader.user, reader.app_password)
        mail.select("inbox")
        status, messages = mail.search(None, "ALL")
        if status != "OK" or not messages[0]:
            print("  FAIL: inbox search failed")
            mail.logout()
            return False
        ids = messages[0].split()[-15:]
        print(f"  PASS: logged in as {reader.user}; scanning last {len(ids)} messages")
        hits = 0
        for msg_id in reversed(ids):
            res, msg_data = mail.fetch(msg_id, "(RFC822.HEADER BODY.PEEK[TEXT])")
            if res != "OK":
                continue
            # fetch RFC822 for extract
            res2, full = mail.fetch(msg_id, "(RFC822)")
            if res2 != "OK":
                continue
            msg = email_mod.message_from_bytes(full[0][1])
            subject, encoding = decode_header(msg.get("Subject", ""))[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8", errors="ignore")
            frm = msg.get("From", "")
            blob = (subject + " " + frm + " " + reader._extract_body(msg)).lower()
            if any(k in blob for k in ("greenhouse", "security code", "airbnb", "doordash", "verification code")):
                pin = reader._extract_pin(subject + " " + reader._extract_body(msg))
                masked = (pin[:2] + "***" + pin[-2:]) if pin else "none"
                print(f"  match subject={subject!r} from={frm[:60]!r} pin={masked}")
                hits += 1
        print(f"  recent matching emails: {hits}")
        mail.logout()
        return True
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return False


async def test_playwright_fill() -> bool:
    print("== Playwright 8-box fill ==")
    from playwright.async_api import async_playwright

    html = """
    <html><body>
      <p>We've sent a security code to your email</p>
      %s
      <button id="submit_app" type="submit">Submit Application</button>
    </body></html>
    """ % "".join(
        f'<input id="security-input-{i}" maxlength="1" />' for i in range(8)
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        inputs = await find_security_inputs(page, wait_seconds=3)
        if len(inputs) != 8:
            print(f"  FAIL: expected 8 inputs, found {len(inputs)}")
            await browser.close()
            return False
        await enter_security_code(page, inputs, "Ab12Cd34")
        values = []
        for i in range(8):
            values.append(await page.input_value(f"#security-input-{i}"))
        joined = "".join(values)
        await browser.close()
        if joined == "Ab12Cd34":
            print(f"  PASS: filled {joined}")
            return True
        print(f"  FAIL: filled {joined!r}")
        return False


def list_pending_jobs() -> None:
    print("== Pending jobs in jobs.db ==")
    conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(__file__)), "jobs.db"))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    for company, like in (("Airbnb", "%airbnb%"), ("DoorDash", "%doordash%")):
        c.execute(
            """
            SELECT job_id, title, status, apply_url
            FROM job_postings
            WHERE (LOWER(company_name) LIKE ? OR apply_url LIKE ?)
              AND status NOT IN ('APPLIED', 'EXPIRED', 'INVALID')
            ORDER BY id DESC LIMIT 5
            """,
            (like, like),
        )
        rows = c.fetchall()
        print(f"  {company}: {len(rows)} recent pending (showing up to 5)")
        for r in rows:
            print(f"    {r['job_id']} | {r['status']} | {r['title'][:60]}")
    conn.close()


async def main():
    results = {
        "extractor": test_extractor(),
        "gmail": test_gmail_login(),
        "playwright": await test_playwright_fill(),
    }
    list_pending_jobs()
    print("\n== Summary ==")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    asyncio.run(main())
