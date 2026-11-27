"""Real Chromium + pinned public proxy smoke, no public internet dependency."""
import asyncio

from playwright.async_api import async_playwright

from . import proxy as netproxy
from .proxy import PinnedProxy
from .server import BrowserRenderer


async def serve_fixture(reader, writer):
    await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
    body = (
        b"<html><head><title>Public JS fixture</title></head><body>"
        b"<main id='content'>Loading...</main>"
        b"<a href='/next'>Read next page</a>"
        b"<script>document.querySelector('#content').textContent="
        b"'Dynamically rendered public article with JavaScript';</script>"
        b"</body></html>"
    )
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
        + b"Content-Length: " + str(len(body)).encode()
        + b"\r\nConnection: close\r\n\r\n" + body
    )
    await writer.drain()
    writer.close()


async def main():
    # The mock backend is loopback only, but the browser requests a TEST
    # public hostname. Only the test's pinned-upstream connector is swapped
    # to the local fixture; no private target becomes an approved URL.
    proxy = await PinnedProxy().start()
    fixture = await asyncio.start_server(serve_fixture, "127.0.0.1", 0)
    fixture_port = fixture.sockets[0].getsockname()[1]
    original_resolve = netproxy.resolve_public
    original_connect = netproxy.asyncio.open_connection

    async def resolve_test_public(host, port):
        if host == "example.org" and port == 8080:
            return "8.8.8.8"
        return await original_resolve(host, port)

    async def connect_test_upstream(host, port, *args, **kwargs):
        if host == "8.8.8.8" and port == 8080:
            return await original_connect("127.0.0.1", fixture_port, *args, **kwargs)
        return await original_connect(host, port, *args, **kwargs)

    netproxy.resolve_public = resolve_test_public
    netproxy.asyncio.open_connection = connect_test_upstream
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                chromium_sandbox=True, headless=True,
                args=[
                    "--disable-quic", "--proxy-bypass-list=<-loopback>",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                ],
            )
            try:
                context = await browser.new_context(
                    proxy={"server": proxy.url}, service_workers="block",
                    accept_downloads=False,
                )
                page = await context.new_page()
                await page.set_content(
                    "<html><body><main id='body'></main>"
                    "<script>document.getElementById('body').innerText="
                    "'Site dinamico renderizado por JavaScript';</script></body></html>"
                )
                assert await page.locator("#body").inner_text() == (
                    "Site dinamico renderizado por JavaScript"
                )
                await context.close()
                print("CHROMIUM_JS_SMOKE_PASS")

                result = await BrowserRenderer(browser, proxy.url).render(
                    "http://example.org:8080/article"
                )
                assert result["status"] == "opened", result
                assert "Dynamically rendered public article" in result["text"], result
                assert any(link["url"].endswith("/next") for link in result["links"]), result
                print("CHROMIUM_PROXY_DOM_SMOKE_PASS")
                raster = await BrowserRenderer(browser, proxy.url).render(
                    "http://example.org:8080/article", capture=True
                )
                assert raster.startswith(b"\x89PNG\r\n\x1a\n") and len(raster) < 2_500_000
                assert int.from_bytes(raster[16:20], "big") == 900
                assert int.from_bytes(raster[20:24], "big") == 700
                print("CHROMIUM_PROXY_CAPTURE_SMOKE_PASS")
            finally:
                await browser.close()
    finally:
        netproxy.resolve_public = original_resolve
        netproxy.asyncio.open_connection = original_connect
        fixture.close()
        await fixture.wait_closed()
        await proxy.close()


if __name__ == "__main__":
    asyncio.run(main())
