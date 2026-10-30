"""
Greenhouse email security-code step.

After Submit, many Greenhouse boards (Airbnb, DoorDash, etc.) show
a security-code field and email an 8-character PIN. This module polls
Gmail and completes that step so the application can actually submit.
"""

import asyncio
import time
from typing import Iterable, List, Optional, Sequence

from gmail_authenticator import GmailVerificationReader

SECURITY_INPUT_SELECTOR = (
    "input[id^='security-input-'], "
    "input[name*='security' i], "
    "input[id*='security' i], "
    "input[autocomplete='one-time-code'], "
    "input[name*='code' i], "
    "input[id*='verification' i], "
    "input[inputmode='numeric'][maxlength], "
    "input[aria-label*='security' i], "
    "input[aria-label*='code' i]"
)

SUBMIT_SELECTOR = (
    "#submit_app, button[type='submit'], input[type='submit'], "
    "button:has-text('Submit Application'), button:has-text('Submit application'), "
    "button:has-text('Submit'), button:has-text('Verify'), button:has-text('Continue')"
)

CONFIRM_PHRASES = (
    "thank you for applying",
    "thank you for your interest",
    "your application has been received",
    "application submitted",
    "thanks for applying",
    "we have received your application",
    "application was submitted",
)

ERROR_PHRASES = (
    "incorrect security code",
    "invalid security code",
    "invalid code",
    "there was an error processing your application",
    "we couldn't submit your application",
    "flagged as possible spam",
)


def _search_roots(page, extra_targets: Optional[Sequence] = None) -> list:
    roots = []
    seen = set()
    for t in list(extra_targets or []) + list(page.frames) + [page]:
        key = id(t)
        if key not in seen:
            seen.add(key)
            roots.append(t)
    return roots


async def find_security_inputs(page, extra_targets: Optional[Sequence] = None, wait_seconds: int = 12) -> List:
    """Wait briefly for Greenhouse security-code inputs to appear."""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        visible = []
        seen_ids = set()
        for root in _search_roots(page, extra_targets):
            try:
                inputs = await root.query_selector_all(SECURITY_INPUT_SELECTOR)
            except Exception:
                continue
            for inp in inputs:
                try:
                    if not await inp.is_visible():
                        continue
                    el_id = await inp.get_attribute("id") or str(id(inp))
                    if el_id in seen_ids:
                        continue
                    seen_ids.add(el_id)
                    visible.append(inp)
                except Exception:
                    continue
        if visible:
            return visible
        await asyncio.sleep(0.75)
    return []


async def page_has_phrases(page, phrases: Iterable[str], extra_targets: Optional[Sequence] = None) -> bool:
    for root in _search_roots(page, extra_targets):
        try:
            text = (await root.content()).lower()
        except Exception:
            try:
                text = (await root.inner_text("body")).lower()
            except Exception:
                continue
        if any(p in text for p in phrases):
            return True
    return False


async def _click_submit(page, extra_targets: Optional[Sequence] = None) -> bool:
    for root in _search_roots(page, extra_targets):
        try:
            btn = await root.query_selector(SUBMIT_SELECTOR)
            if btn and await btn.is_visible() and await btn.is_enabled():
                await btn.click(force=True)
                return True
        except Exception:
            continue
    return False


async def enter_security_code(page, inputs: List, code: str) -> None:
    """Fill Greenhouse's 8 separate security-input-* boxes (React-safe)."""
    code = (code or "").strip()
    if not code or not inputs:
        return

    filled = 0
    try:
        filled = await inputs[0].evaluate(
            """(el, code) => {
                const doc = el.ownerDocument;
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                let n = 0;
                for (let i = 0; i < code.length; i++) {
                    const box = doc.getElementById('security-input-' + i);
                    if (!box) break;
                    box.focus();
                    setter.call(box, code[i]);
                    box.dispatchEvent(new Event('input', { bubbles: true }));
                    box.dispatchEvent(new Event('change', { bubbles: true }));
                    n += 1;
                }
                return n;
            }""",
            code,
        )
    except Exception:
        filled = 0

    if filled == len(code):
        return

    if len(inputs) >= len(code):
        for idx, char in enumerate(code):
            try:
                await inputs[idx].fill(char)
                await asyncio.sleep(0.08)
            except Exception:
                continue
        return

    try:
        await inputs[0].fill(code)
    except Exception:
        try:
            await inputs[0].click()
            await page.keyboard.type(code, delay=40)
        except Exception:
            pass


async def handle_greenhouse_security_code(
    page,
    extra_targets: Optional[Sequence] = None,
    sender_keywords: Optional[Sequence[str]] = None,
    email_addr: Optional[str] = None,
    received_after_ts: Optional[float] = None,
) -> bool:
    """
    If a Greenhouse email security-code prompt is present, fetch the PIN
    from Gmail, enter it, and click Submit again.

    Returns True when a prompt was found and a code was entered.
    Returns False when no prompt appeared or Gmail did not yield a code.
    """
    inputs = await find_security_inputs(page, extra_targets=extra_targets, wait_seconds=12)
    if not inputs:
        return False

    print(f"📨 Greenhouse security code requested ({len(inputs)} input box(es)). Polling Gmail...")
    reader = GmailVerificationReader(user=email_addr) if email_addr else GmailVerificationReader()
    keywords = list(sender_keywords or ("greenhouse",))
    if "greenhouse" not in [k.lower() for k in keywords]:
        keywords.append("greenhouse")

    code = None
    for i, kw in enumerate(keywords):
        timeout = 45 if i == 0 else 20
        code = await asyncio.to_thread(
            reader.fetch_latest_verification_code,
            kw,
            timeout,
            4,
            received_after_ts,
        )
        if code:
            break

    if not code:
        print("⚠️ Could not retrieve a fresh Greenhouse security code from Gmail.")
        return False

    print(f"🔑 Entering Greenhouse security code: {code}")
    await enter_security_code(page, inputs, code)
    await asyncio.sleep(0.8)

    clicked = await _click_submit(page, extra_targets)
    if clicked:
        print("🚀 Re-submitted application with email security code.")
        await asyncio.sleep(4.0)
    else:
        print("⚠️ Security code entered but a follow-up Submit button was not found.")
    return True
