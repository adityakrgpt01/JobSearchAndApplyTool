import asyncio
import os
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

PROOF_DIR = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots"
USER_DATA_DIR = "./chrome_user_data_teradata"

async def run():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            channel="chrome",
            headless=False,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1400, "height": 950}
        )
        page = context.pages[0] if context.pages else await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        
        url = "https://careers.teradata.com/jobs/220870/senior-ai-engineer/apply"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        
        # Remove TrustArc overlay
        await page.evaluate("""() => {
            const el = document.getElementById('truste-consent-track') || document.querySelector('[class*="truste"]');
            if (el) el.remove();
        }""")
        
        # Inspect all inputs, selects, and text fields
        print("--- INSPECTING FORM FIELDS ---")
        fields = await page.evaluate("""() => {
            const inputs = Array.from(document.querySelectorAll('input, select, textarea, button, [role="button"], [role="combobox"], [role="listbox"]'));
            return inputs.map((el, idx) => ({
                idx,
                tag: el.tagName,
                id: el.id || '',
                name: el.name || '',
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                ariaLabelledBy: el.getAttribute('aria-labelledby') || '',
                text: el.innerText ? el.innerText.trim().slice(0, 40) : '',
                parentText: el.parentElement ? el.parentElement.innerText.trim().slice(0, 40) : ''
            })).filter(f => f.id || f.name || f.ariaLabel || f.ariaLabelledBy || f.text);
        }""")
        for f in fields:
            print(f)
                
        await context.close()

if __name__ == "__main__":
    asyncio.run(run())

