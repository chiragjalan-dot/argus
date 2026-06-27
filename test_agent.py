"""Quick test -- run one task through the browser agent loop."""
import asyncio, sys
sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright
from browser_agent import agent_loop, connect_chrome, CDP_URL

TASK = "Take a screenshot and tell me what page is currently open and what you can see on it."

async def main():
    async with async_playwright() as p:
        browser = await connect_chrome(p)
        ctx = browser.contexts[0]
        pages = [pg for pg in ctx.pages if pg.url != "about:blank"] or ctx.pages
        page = pages[0]
        await page.bring_to_front()
        print(f"Connected. Active tab: {await page.title()!r}")
        await agent_loop(TASK, page, ctx)
        await browser.close()

asyncio.run(main())
