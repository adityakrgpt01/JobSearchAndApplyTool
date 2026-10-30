#!/usr/bin/env python3
import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        url = "https://jobs.ashbyhq.com/anyscale/8b29c5e5-d56f-4c52-8887-a6a1827cf042/application"
        await page.goto(url, wait_until="networkidle")

        inputs = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('input, select, textarea, button[aria-haspopup], [role="radio"]')).map(el => {
                let lbl = '';
                const parent = el.closest('div[class*="field"], div[class*="Question"], div[class*="Container"], label');
                if (parent) {
                    const l = parent.querySelector('label, h3, h4, p, span');
                    if (l) lbl = l.innerText;
                }
                return {
                    tag: el.tagName,
                    type: el.type,
                    id: el.id,
                    name: el.name,
                    role: el.getAttribute('role'),
                    aria: el.getAttribute('aria-label'),
                    placeholder: el.placeholder || '',
                    label: lbl.replace(/\\n+/g, ' ').substring(0, 60),
                    val: el.value || ''
                };
            });
        }''')
        for idx, i in enumerate(inputs):
            print(f"[{idx}] tag={i['tag']} type={i['type']} name={i['name']} aria='{i['aria']}' label='{i['label']}' ph='{i['placeholder']}'")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
