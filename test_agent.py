"""Quick test -- run one task through the browser agent loop."""
import asyncio, sys
sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright
from browser_agent import agent_loop, connect_chrome, CDP_URL

TASK = (
    "Navigate to https://developers.facebook.com. "
    "Use find_visual to locate the profile avatar or account icon in the top-right corner. "
    "Click it with the returned coordinates. Take a screenshot after. "
    "Report the logged-in account name shown in the dropdown or menu that appears."
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
