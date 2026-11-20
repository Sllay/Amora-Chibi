"""Real Chromium execution smoke with no external websites or private URL calls."""
import asyncio

from playwright.async_api import async_playwright

from .proxy import PinnedProxy, ProxyDenied, public_ip_answers
from .server import BrowserRenderer, checked_web_url


async def main():
    proxy = await PinnedProxy().start()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                chromium_sandbox=True,
                headless=True,
                args=["--disable-quic", "--proxy-bypass-list=<-loopback>"],
            )
            try:
                context = await browser.new_context(
                    proxy={"server": proxy.url},
                    service_workers="block",
                    accept_downloads=False,
                )
                page = await context.new_page()
                await page.set_content(
                    "<html><body><main id='body'></main>"
                    "<script>document.getElementById('body').innerText="
                    "'Site dinamico renderizado por JavaScript';</script></body></html>"
                )
                text = await page.locator("#body").inner_text()
                assert text == "Site dinamico renderizado por JavaScript", text
                await context.close()
                print("CHROMIUM_JS_SMOKE_PASS")
            finally:
                await browser.close()
    finally:
        await proxy.close()


if __name__ == "__main__":
    asyncio.run(main())
