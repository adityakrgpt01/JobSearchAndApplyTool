"""Shared Workday wizard guards used by every company applier except NVIDIA and Mastercard."""

import asyncio
import os
from typing import Any, Optional

from database import update_job_status


async def _page_text(page) -> str:
    try:
        return (await page.inner_text("body", timeout=5000)).lower()
    except Exception:
        try:
            return (await page.content()).lower()
        except Exception:
            return ""


async def is_workday_crash_page(page) -> bool:
    """Workday's generic failure screen: the progress bar stays and the form never renders."""
    text = await _page_text(page)
    return "something went wrong" in text or "please refresh the page" in text


def autofill_entry_url(clean_url: str, target_url: str) -> str:
    """Open the resume-autofill step directly instead of the intermediate Apply landing page."""
    if "/apply" not in clean_url and "/job/" in clean_url:
        return clean_url + "/apply/autofillWithResume"
    if clean_url.endswith("/apply"):
        return clean_url + "/autofillWithResume"
    return target_url


async def step_guard(page, applier: Any, label: str):
    """
    Recover from a Workday crash page or a Cloudflare check.
    Returns "ok", "continue", or ("fatal", message).
    """
    if await is_workday_crash_page(page):
        applier._wd_crash_reloads = getattr(applier, "_wd_crash_reloads", 0) + 1
        if applier._wd_crash_reloads > 3:
            return (
                "fatal",
                f"{label} Workday showed 'Something went wrong' and did not recover after 3 refreshes.",
            )
        print(f"⚠️ {label} Workday error page ('Something went wrong'). Refreshing ({applier._wd_crash_reloads}/3)...")
        try:
            await page.reload(wait_until="domcontentloaded", timeout=35000)
        except Exception as reload_err:
            print(f"⚠️ Refresh note: {reload_err}")
        await asyncio.sleep(4.0)
        return "continue"

    content_low = await _page_text(page)
    widget = await page.query_selector(
        "iframe[src*='challenges.cloudflare.com'], iframe[title*='Cloudflare' i]"
    )
    if "security check" in content_low or "verify you are human" in content_low or widget:
        applier._cf_attempts = getattr(applier, "_cf_attempts", 0) + 1
        if applier._cf_attempts > 4:
            return (
                "fatal",
                f"{label} Workday is showing a Cloudflare 'Verify you are human' check that did not clear.",
            )
        print(f"🛡️ {label} Cloudflare security check. Clicking 'Verify you are human' ({applier._cf_attempts}/4)...")
        clicked = False
        if widget:
            box = await widget.bounding_box()
            if box:
                await page.mouse.click(box["x"] + 28, box["y"] + box["height"] / 2)
                clicked = True
        if not clicked:
            try:
                await page.get_by_text("Verify you are human").click(timeout=4000)
                clicked = True
            except Exception:
                pass
        cleared = False
        for _ in range(8):
            await asyncio.sleep(2.0)
            content_low = await _page_text(page)
            if "verify you are human" not in content_low and "security check" not in content_low:
                cleared = True
                break
        if cleared:
            print("✅ Cloudflare check cleared.")
            applier._cf_attempts = 0
        else:
            print("⏳ Cloudflare check still on screen.")
        return "continue"

    return "ok"


async def attach_resume_if_missing(page, profile: dict) -> bool:
    """
    Attach the candidate resume on the Autofill step.
    Returns False when the page still has no resume, so the caller must not click Continue.
    """
    already = await page.query_selector("div[data-automation-id='file-upload-item']")
    if already:
        return True
    file_input = await page.query_selector(
        "input[data-automation-id='file-upload-input-ref'], input[type='file']"
    )
    resume_path = (profile.get("resume") or {}).get("file_path", "resume.pdf")
    if file_input and resume_path and os.path.exists(resume_path):
        print(f"📎 Attaching candidate resume before Continue: {resume_path}...")
        await file_input.set_input_files(resume_path)
        await asyncio.sleep(6.0)
        already = await page.query_selector("div[data-automation-id='file-upload-item']")
        return already is not None
    return False


def record_apply_result(job: dict, ok: bool, msg: str, dry_run: bool, slug: str):
    """Write the dashboard status after a dedicated applier finishes."""
    text = (msg or "").lower()
    if ok and dry_run:
        status = "READY_TO_SUBMIT (DRY_RUN)"
    elif ok and any(k in text for k in ["submitted", "applied", "received"]):
        status = "APPLIED"
    elif ok:
        status = "READY_TO_SUBMIT"
    else:
        status = "FAILED"
    shot = f"screenshots/{slug}_{job.get('job_id', 'latest')}.png"
    update_job_status(job.get("job_id", ""), status, msg, shot)


async def hold_headed_browser(page, job_id: str, screenshot_path: Optional[str] = None):
    """Leave a headed browser on screen until the candidate closes it or a confirmation appears."""
    print(f"[{job_id}] Leaving the application open on screen for review.")
    shot = screenshot_path or f"screenshots/{job_id}.png"
    submitted = False
    for _ in range(3600):
        try:
            if page.is_closed():
                break
            content = (await page.inner_text("body")).lower()
            curr_url = page.url.lower()
            confirmed = any(w in content for w in [
                "thank you for your interest",
                "your application has been received",
                "application submitted",
                "thanks for applying",
                "we have received your application",
                "thank you for applying",
                "successfully submitted",
                "application has been submitted",
            ]) or any(k in curr_url for k in ["/confirmation", "/submitted", "/thank-you", "/thanks"])
            if confirmed and not submitted:
                submitted = True
                try:
                    os.makedirs(os.path.dirname(shot) or ".", exist_ok=True)
                    await page.screenshot(path=shot, full_page=True, timeout=12000)
                except Exception:
                    pass
                update_job_status(
                    job_id,
                    "APPLIED",
                    "Application submitted by candidate via live browser [Confirmation Verified]",
                    shot,
                )
                print(f"🎉 [{job_id}] Submission confirmed. Marked APPLIED.")
                break
        except Exception:
            break
        await asyncio.sleep(1.2)
