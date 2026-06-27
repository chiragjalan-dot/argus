"""Quick test -- run one task through the browser agent loop."""
import asyncio, sys
sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright
from browser_agent import agent_loop, connect_chrome, CDP_URL

TASK = (
    "Navigate to https://www.facebook.com/settings and take a screenshot. "
    "Read the page and extract the account name, email, and phone number shown in the settings. "
    "If the page asks to log in, we are already logged in — dismiss any overlay and try again. "
    "Report all account details visible."
)

async def main():
    async with async_playwright() as p:
        browser = await connect_chrome(p)
        ctx = browser.contexts[0]
        pages = [pg for pg in ctx.pages if pg.url != "about:blank"] or ctx.pages
        page = pages[0]
        print(f"Connected. Active tab: {await page.title()!r}")
        await agent_loop(TASK, page, ctx)
        await browser.close()

asyncio.run(main())
