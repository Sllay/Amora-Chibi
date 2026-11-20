# Amora Web — Run 2: Chromium on-demand, protected worker

**Release intent:** optional feature. The Core from Run 1 works unchanged without the worker. **Code or CI green ≠ actual deployment or Discord E2E.**

## Architecture

```text
Verified BK owner Discord DM → Core cognition (Run 1 web intent)
    ├─ public URL HTTP direct with pinned-IP DNS
    ├─ when blocked, empty, JS shell or explicitly "com navegador" → BrowserClient
    │     ↳ HTTP POST /render, URL only + shared token; no user memory or prompts
    │           ↳ separate non-root, single-slot Chromium worker
    │                 ↳ per-render incognito context (no persistence/downloads)
    │                 ↳ JS execution + visible DOM text and hyperlinks
    │                 ↳ HTTP proxy that validates ALL DNS answers and pins IP
    │                 ↳ route: GET/HEAD only, no private URLs, no WebSocket,
    │                    no Service Workers; no image/media/font downloads
    └─ evidence with canonical=false, URL and timestamp → normal social reply
```

## Deploy on Northflank or compatible host

**Do not install Chromium into Amora Core's Alpine container.** Create a **separate private container service**, Dockerfile path `workers/browser/Dockerfile`, build context repository root, exposed port **8097**. Its official base image contains the matching Chromium for Playwright 1.64.1, so it is much larger than Core. Initial capacity guidance: **at least 1 vCPU and approximately 1 GB RAM** for a single-browser service; real peak needs to be measured. A 0.2-vCPU/512-MB free Core instance is not certified to host Chromium. Limit requests/concurrency to one rendering slot and avoid autoscaling beyond budget. No GPU required.

Generate **one random secret at least 32 characters**. Supply the exact same value to these services as `AMORA_WEB_BROWSER_TOKEN`. Never store it in a repository or send it in Discord.

Worker environment:
- `AMORA_WEB_BROWSER_TOKEN=<secret>` (required; worker fails startup if missing/short)
- Port `8097` internally, no public route recommended.
- Non-root `pwuser`, enabled Chromium sandbox; production must allow unprivileged user namespaces with an **audited seccomp profile** (see [Playwright Docker guidance](https://playwright.dev/python/docs/docker)) and no privileged container.
- **Crucial:** restrict network egress at the platform level to public internet TCP web destinations and required DNS, denying private/link-local/metadata networks. The built-in pinned DNS proxy protects *normal browser traffic*, but Chromium is untrusted code; a compromised process could bypass an in-process proxy without a network policy. Do not expose the browser worker until network isolation is enforced and verified.
- `GET /health` reports worker readiness; `POST /render` requires the shared secret. Neither endpoint is for arbitrary end users.

Core environment:
- `AMORA_WEB_ENABLED=1` (default).
- `AMORA_WEB_BROWSER_URL=https://<private-or-protected-worker-host>` (required to activate browser; no trailing `/render`).
- `AMORA_WEB_BROWSER_TOKEN=<same-secret>`.
- For **verified private network transport only**, `AMORA_WEB_BROWSER_PRIVATE_HTTP=1` permits `http://worker-host:8097` when the host platform cannot provide internal TLS. Do not use this on public internet.
- If either URL or token is absent/invalid, Core stays on Run 1 (no Chromium execution, no new Core dependency). Deploying code without a configured worker does not activate JS navigation.

## Behavior and limits

1. URLs and selected numbered follow-links first use Run 1 direct HTTP. Chromium is attempted only if the static response fails, is empty/very short or resembles a JS/browser challenge; also on explicit "com navegador" requests. Long, valid static HTML avoids Chromium.
2. Search results still originate from independent Run 1 search mechanisms (SearXNG configured; Bing RSS / DuckDuckGo HTML best effort). Only when both initial candidates lack readable HTTP evidence does a single browser-render attempt read the first result.
3. The browser creates a fresh context for each request, enables JS, retrieves public HTML and visible DOM content and follows redirects under the proxy. It returns at most 2,400 characters, 12 external references, timestamp and source URL. No cookies, scripts or login state persist between requests. Authenticated website sessions, CAPTCHA solving and arbitrary form submissions are **not** included.
4. Bounded timings: navigation ~8s, worker ~10s, Core browser HTTP client ~11s, Core total web retrieval ~15s. Only one active renderer; further requests get a busy error. Actual performance/cost needs live measurement.
5. Browser response is **untrusted web evidence** in the Core TurnFrame, not fact, identity, instruction or memory. Core still owns citation URLs and source gating. If browser fails, preserve valid static evidence or original HTTP failure; never describe unavailable content as read.
6. A website can restrict automation by login, anti-bot, geographic restrictions or CAPTCHA; there is no honest guarantee to bypass those controls. Sites with longer-running JavaScript might exceed this bounded Run 2 budget.

## Validation

Tests (mocked network, no paid external service):
```sh
python -m pytest tests/test_web_tools_run1.py tests/test_web_browser_run2.py -q
```
The `Web browser worker` GitHub workflow must build the actual non-root Playwright image and execute `python -m workers.browser.smoke` in Chromium, including JavaScript DOM rendering. Its CI-only seccomp override is **not** the production seccomp policy.

**Required before actual public internet navigation:** worker-only network policy with deny-private egress; confirm `/render` rejects unauthorized or oversized requests; actual Northflank RAM/CPU and sandbox; Core ↔ private worker URL/token; Discord owner DM; a real JS site, HTTP 403/CAPTCHA case, redirect-to-private, link following, and effects on timeouts. Do not claim production ready until these live checks are recorded.
