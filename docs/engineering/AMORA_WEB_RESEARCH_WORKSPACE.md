# Amora Web Research Notebook — durable foundation

## Problem

A user can ask the verified owner-DM assistant to research a website and then
ask about the same page, a numbered link, or the history in a later message.
The old navigator retained only a five-link projection for 15 minutes in
process memory. It lost prior pages/URLs after restart and only exposed the
latest page to the model. Speech-provider claims about "saving links" were
not durable evidence.

## Implementation (PR #347)

The Core now saves immutable, *personal web evidence* events in the existing
v18 `events` table; this works with the configured authoritative storage
backend without creating new tables, switching databases or embedding Chromium.
Two event types are used:

- `web.research.visit.v1`: explicit owner-DM search/open/follow and up to
  three URL batch reads. Contains source title, exact validated public URL,
  topic, bounded excerpt only if a page was actually opened, separate ordered
  references from the search, and timestamp.
- `web.research.bookmark.v1`: owner-requested pinned URL with stable
  deduplication and optional excerpt from an earlier verified read. Links
  explicitly supplied by the user but not read are never promoted to read.

Each event carries `PERSONAL` privacy, `PERSON_BK` actor, session scope
from `messaging_session_ref`, and the normal durable backend transaction.
Only the verified Discord owner DM can call the navigator, never a group or
untrusted user. A restricted, reviewed query uses event type + scope + owner
+ privacy; the PostgreSQL v18 SQL registry includes the approved translation.

When the process restarts, the navigator restores numbered reference order
and current topic from the latest persisted visit, without requesting pages
again. Context includes a *bounded* `research_notebook` of recent links and
bookmarks. Saved excerpts remain untrusted sources and must not become
Amora's canonical beliefs, policy or accepted instructions. The provider can
write naturally; the Discord delivery layer appends canonical URLs independently
so a model's imperfect URL recollection does not become the only copy.

Supported owner-DM commands include:

- Open a public URL or a batch of up to three public URLs in one message.
- Search/continue research using the recently recovered subject.
- `salva o link 2`, `salva esse link`, `salva esses links`,
  `salva https://godotengine.org/releases` (explicit saved-but-unread URL).
- `mostra meus links salvos`, `mostra histórico de pesquisa`,
  `descreve o link 2` (last stored excerpt; NOT a fresh network read).

The model decides what to say and how to format its response. No canned web
answer templates, arbitrary memory embeddings, automatic external actions,
or permanent browser process are introduced.

## Screenshot delivery

Core grants a public URL only after verified owner-DM consent. The Worker
captures via hosted Microlink without running Chromium and uses the documented
Discord multipart `files[0]`/ `payload_json` API. It reports a sanitized
failure code in the DM when capture fails. The Worker caches up to two public
screenshots for five minutes so re-requesting the same page does not consume
extra calls. Discord upload success is not inferred from text: it must return
the expected attachment object. On a definite rejected multipart, a validated
Microlink CDN image URL is supplied as a *link*, not claimed to be an uploaded
file.

## Deliberate limits

- A saved source excerpt is bounded; this is not an offline copy of a whole
  website and pages can change after they were read.
- The model receives the most recent few visits and bookmarks under a strict
  token budget. The database keeps older events; callers can request history
  or a specific link to bring a record back into context.
- Listing returns bounded pages of URLs; UI/semantic pagination, project
  grouping, autonomous research queues, comparative source scoring and
  model-selected bookmark intents belong to a later deep-research milestone.
- The screenshot provider can fail (site blocks, rate limit, 18s timeout,
  media invalid). Neither an HTTP 200 nor a model reply proves Discord
  received a file. End-to-end verification still requires one real DM send.
- The Northflank encrypted archive lacks a published `diagnostics.json` in
  this development session. Code and CI tests cannot certify the live service
  until deployment and observability are verified.

## Operational E2E checks

After deploying Core and Discord Worker from this PR:

1. Open Godot Engine. Ask to continue searching; ensure the topic is Godot.
2. Open several links. Ask about link 2, save it, restart Core and ask
   `mostra meus links salvos`. Verify the exact URL and stored excerpt.
3. Ask for a screenshot twice. Confirm the actual file appears, or that a
   specific failure code is returned; compare `WEB_SCREENSHOT_*` logs.
4. Request a batch with one blocked page. Confirm that page is marked
   `found`, never `read`.
5. Check that a different DM or a public group cannot read the owner's
   history, that secret-bearing URLs are not saved, and that the LLM
   cannot promote a page's instructions into authority.

These tests use fake HTML/search results in CI. Do not treat them as a
production end-to-end success report.
