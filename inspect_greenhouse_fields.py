#!/usr/bin/env python3
import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        url = "https://job-boards.greenhouse.io/twilio/jobs/8015771"
        await page.goto(url, wait_until="networkidle")

        controls = await page.evaluate('''() => {
            const list = [];
            document.querySelectorAll('input, select, textarea, [role="combobox"], [role="listbox"], button[aria-haspopup]').forEach(el => {
                let lbl = '';
                if (el.id) {
                    const l = document.querySelector(`label[for="${el.id}"]`);
                    if (l) lbl = l.innerText;
                }
                if (!lbl) lbl = el.getAttribute('aria-label') || el.name || el.id || el.closest('div[class*="field"], label')?.innerText || '';
                list.push({
                    tag: el.tagName,
                    type: el.type || '',
                    id: el.id,
                    name: el.name,
                    role: el.getAttribute('role'),
                    label: lbl.replace(/\\n+/g, ' ').substring(0, 70),
                    val: el.value || el.innerText || ''
                });
            });
            return list;
        }''')

        for idx, c in enumerate(controls):
            print(f"[{idx}] tag={c['tag']} type={c['type']} id={c['id']} name={c['name']} role={c['role']} label='{c['label']}'")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
