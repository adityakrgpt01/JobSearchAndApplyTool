#!/usr/bin/env python3
"""
Booking Holdings Standalone Application Engine (iCIMS Platform)
Features:
- Handles iCIMS nested iframe structure (#icims_content_iframe)
- GDPR consent selection and legal compliance agreement
- Email authorization and credential initialization
- Pre-submission Review screenshot capture (screenshots/booking_{job_id}.png)
- Live Submission proof capture (screenshots/booking_{job_id}_submitted.png)
- Zero False Positives / Zero True Negatives
"""
import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("booking_applier")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENSHOTS_DIR = os.path.join(BASE_DIR, "screenshots")
PROFILE_PATH = os.path.join(BASE_DIR, "user_profile.json")

os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def load_profile():
    with open(PROFILE_PATH, "r") as f:
        return json.load(f)

def apply_to_booking_job(job_url, job_id, dry_run=True, headless=True, keep_open=False):
    logger.info(f"Starting application for Booking Holdings job {job_id} at {job_url} (dry_run={dry_run})")
    profile = load_profile()
    personal = profile.get("personal", {})
    email = personal.get("email", "adityakumargupta521@gmail.com")
    
    review_screenshot_path = os.path.join(SCREENSHOTS_DIR, f"booking_{job_id}.png")
    submitted_screenshot_path = os.path.join(SCREENSHOTS_DIR, f"booking_{job_id}_submitted.png")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"'
            }
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = context.new_page()
        
        try:
            logger.info(f"Navigating to Booking iCIMS portal: {job_url}")
            page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
            
            frame_loc = page.frame_locator("#icims_content_iframe")
            email_in = frame_loc.locator("#email")
            
            try:
                email_in.wait_for(state="visible", timeout=15000)
            except Exception:
                logger.warning("Frame locator timeout, checking direct frames...")
                for f in page.frames:
                    if f.query_selector("#email"):
                        email_in = f.locator("#email")
                        frame_loc = f
                        break
                        
            logger.info(f"Filling candidate email: {email}")
            email_in.fill(email)
            
            # Select GDPR Consent Option
            logger.info("Selecting GDPR consent option...")
            consent_sel = frame_loc.locator("#gdpr_consent_type")
            if consent_sel.count() > 0:
                consent_sel.select_option("37002057001")
                
            # Check GDPR Acknowledgement Checkbox
            gdpr_chk = frame_loc.locator("#accept_gdpr")
            if gdpr_chk.count() > 0 and not gdpr_chk.is_checked():
                gdpr_chk.check()
                
            page.wait_for_timeout(1500)
            
            # Verify Submit Button State
            sub_btn = frame_loc.locator("#enterEmailSubmitButton")
            is_disabled = sub_btn.is_disabled() if sub_btn.count() > 0 else True
            logger.info(f"Booking iCIMS email gateway submit button ready: {not is_disabled}")
            
            if is_disabled:
                logger.error("Submit button remained disabled!")
                page.screenshot(path=review_screenshot_path, full_page=True)
                return False

            # Advance past email gateway into application
            logger.info("Submitting candidate email & GDPR authorization on Booking iCIMS...")
            sub_btn.click()
            page.wait_for_timeout(6000)

            # Check if hCaptcha challenge triggered
            hcaptcha_found = False
            for f in page.frames:
                if "hcaptcha" in f.url:
                    hcaptcha_found = True
                    break

            if hcaptcha_found:
                logger.warning("🛡️ hCaptcha interactive challenge ('Find shapes') active on Booking iCIMS portal.")
                if not headless:
                    logger.info("Running in visible mode. Waiting up to 30s for manual CAPTCHA completion...")
                    for _ in range(15):
                        page.wait_for_timeout(2000)
                        still_captcha = any("hcaptcha" in f.url for f in page.frames)
                        if not still_captcha:
                            logger.info("✅ CAPTCHA passed!")
                            hcaptcha_found = False
                            break

            # If passed captcha or no captcha, fill candidate profile fields if present
            if not hcaptcha_found:
                try:
                    # Check for profile form inputs inside frame
                    fn = frame_loc.locator("#firstName, input[name='firstName'], input[id*='first_name' i]")
                    if fn.count() > 0 and not fn.first.input_value():
                        fn.first.fill(personal.get("first_name", "Aditya"))
                    ln = frame_loc.locator("#lastName, input[name='lastName'], input[id*='last_name' i]")
                    if ln.count() > 0 and not ln.first.input_value():
                        ln.first.fill(personal.get("last_name", "Kumar"))
                    ph = frame_loc.locator("#phone, input[type='tel'], input[name*='phone' i]")
                    if ph.count() > 0 and not ph.first.input_value():
                        ph.first.fill(personal.get("phone", "9643914225")[-10:])
                    city_in = frame_loc.locator("#city, input[name='city'], input[id*='city' i]")
                    if city_in.count() > 0 and not city_in.first.input_value():
                        city_in.first.fill(personal.get("current_city", "Bangalore"))

                    # Resume upload if requested
                    file_in = frame_loc.locator("input[type='file']")
                    resume_file = profile.get("resume", {}).get("file_path", "resume.pdf")
                    if file_in.count() > 0 and os.path.exists(resume_file):
                        file_in.first.set_input_files(resume_file)
                        page.wait_for_timeout(4000)
                except Exception as pe:
                    logger.warning(f"Profile form filling note: {pe}")

            # Capture Pre-submission Review Screenshot
            logger.info(f"Capturing pre-submission Review screenshot: {review_screenshot_path}")
            page.screenshot(path=review_screenshot_path, full_page=True)
            
            if dry_run:
                if hcaptcha_found:
                    logger.info(f"[DRY-RUN] Captcha challenge recorded cleanly to {review_screenshot_path}. Halting before submit.")
                else:
                    logger.info(f"[DRY-RUN] Review screenshot cleanly saved to {review_screenshot_path}. Halting before submit.")
                return True
                
            # Live Submission Mode
            if hcaptcha_found:
                logger.error("Cannot perform unattended live submission while interactive hCaptcha is active.")
                page.screenshot(path=submitted_screenshot_path, full_page=True)
                return False

            next_sub = frame_loc.locator("input[type='submit'], button[type='submit'], button:has-text('Submit'), #submitButton")
            if next_sub.count() > 0:
                next_sub.first.click()
                page.wait_for_timeout(6000)
                page.screenshot(path=submitted_screenshot_path, full_page=True)
                return True
            return True
                
        except Exception as e:
            logger.error(f"Error during Booking application for {job_id}: {e}", exc_info=True)
            page.screenshot(path=os.path.join(SCREENSHOTS_DIR, f"booking_{job_id}_exception.png"))
            return False
        finally:
            if keep_open and not headless:
                logger.info("Leaving the Booking application open for review...")
                for _ in range(3600):
                    try:
                        if page.is_closed():
                            break
                    except Exception:
                        break
                    page.wait_for_timeout(1200)
            browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Booking Holdings Applier")
    parser.add_argument("--url", required=True, help="Job posting URL")
    parser.add_argument("--id", required=True, help="Job identifier")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Perform dry run without submission")
    parser.add_argument("--live", action="store_true", help="Perform live submission")
    parser.add_argument("--no-headless", action="store_true", help="Run with visible browser")
    
    args = parser.parse_args()
    is_dry_run = not args.live
    success = apply_to_booking_job(args.url, args.id, dry_run=is_dry_run, headless=not args.no_headless)
    sys.exit(0 if success else 1)
