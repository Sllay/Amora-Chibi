"""Bounded exploratory web search. Sources are evidence, not verified truth.

The first increment uses the existing public-network transports and notebook.
It deliberately does not claim semantic contradiction detection or fresh
recovery after restart. All limits are local budgets, not provider quotas.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .navigator import (
    WebFetchError, _bing_rss_search, _ddg_search, _searx_search,
    _rank_search_results, _fold_search, _topic_terms, _clean,
)
from .remote_reader import _shareable_public_url

MAX_SEARCH_CALLS = 4
MAX_REFERENCES = 10
MAX_READS = 5
MAX_EVIDENCE_CHARS = 1500
DEEP_SECONDS = 14.0
DISCOVERY_SECONDS = 5.5
CHECKPOINT_TTL_SECONDS = 900


def _fresh_timestamp(value, now):
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return at.tzinfo is not None and 0 <= (now - at).total_seconds() <= CHECKPOINT_TTL_SECONDS
    except (TypeError, ValueError, OverflowError):
        return False


def resumable_checkpoint(record, topic, *, now=None):
    """Only the latest owner-scoped, exact-topic, fresh deep pass qualifies."""
    now = now or datetime.now(timezone.utc)
    return (isinstance(record, dict) and record.get("action") == "search"
            and record.get("depth") == "deep" and record.get("resume_eligible") is True
            and record.get("topic") == topic and bool(topic)
            and _fresh_timestamp(record.get("discovered_at"), now)
            and isinstance(record.get("references"), list) and bool(record["references"]))



def _queries(topic: str) -> list[str]:
    # Keep the original subject on every route, not a hallucinated new topic.
    # Reserve room for suffixes and avoid cutting an opaque word in half.
    short = topic[:190].rsplit(" ", 1)[0] if len(topic) > 190 and " " in topic[:190] else topic[:190]
    return list(dict.fromkeys([topic[:240], short + " documentação",
                              short + " limitações", short + " comparação"]))


def _varied(rows):
    # Diversity of hostnames, NOT proof of editorial independence.
    selected, rest, seen = [], [], set()
    for row in rows:
        host = urlsplit(row["url"]).hostname
        if host in seen:
            rest.append(row)
        else:
            selected.append(row)
            seen.add(host)
    return (selected + rest)[:MAX_REFERENCES]


async def _collect(coros, *, deadline):
    """Keep completed work at deadline; cancel/join only unfinished awaiters."""
    tasks = [asyncio.create_task(coro) for coro in coros]
    if not tasks:
        return [], False
    try:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, pending = await asyncio.wait(tasks, timeout=remaining)
        results = []
        for index, task in enumerate(tasks):
            if task in done and not task.cancelled():
                try:
                    results.append((index, task.result()))
                except (WebFetchError, TimeoutError):
                    results.append((index, None))
                except Exception:
                    results.append((index, None))
        return results, bool(pending)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def deep_search(nav, topic: str, text: str, session_id: str, *,
                      seconds: float = DEEP_SECONDS, resume=None) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    discovery_deadline = min(deadline, loop.time() + min(DISCOVERY_SECONDS, seconds * .4))
    now = datetime.now(timezone.utc)
    resume = resume if resumable_checkpoint(resume, topic, now=now) else None
    discovered_at = resume["discovered_at"] if resume else now.isoformat()
    queries = _queries(topic)
    endpoint = os.getenv("AMORA_WEB_SEARCH_ENDPOINT", "").strip()
    engines = [(nav.search_provider, "configured", False)] if nav.search_provider else (
        ([(_searx_search, "searxng", True)] if endpoint else [])
        + [(_bing_rss_search, "bing_rss", False), (_ddg_search, "ddg_html", False)]
    )
    # Use diverse engines first, then alternate grounded queries. Four total
    # calls; semaphore in navigator also bounds real outstanding threads.
    jobs = [(queries[0], *engine) for engine in engines]
    for i, query in enumerate(queries[1:]):
        jobs.append((query, *engines[i % len(engines)]))
    jobs = [] if resume else jobs[:MAX_SEARCH_CALLS]
    errors = []
    rows = {}
    attempted = 0
    partial = False
    if resume:
        # Preserve the actual discovery order rather than reranking a saved list.
        for ref in resume["references"][:MAX_REFERENCES]:
            if not isinstance(ref, dict):
                continue
            try:
                url = _shareable_public_url(str(ref.get("url") or ""))
            except WebFetchError:
                continue
            rows.setdefault(url, {"url": url, "title": _clean(str(ref.get("title") or ""), 180),
                                  "snippet": _clean(str(ref.get("snippet") or ""), 160),
                                  "discovery_engine": "workspace_checkpoint"})

    async def search_one(job):
        query, fn, engine, needs_endpoint = job
        try:
            result = await nav._call(fn, query, endpoint) if needs_endpoint else await nav._call(fn, query)
        except WebFetchError as exc:
            return engine, [], exc.code
        except TimeoutError:
            return engine, [], "WEB_SEARCH_TIMEOUT"
        ranked = _rank_search_results(result or [], topic)
        safe = []
        for row in ranked:
            try:
                url = _shareable_public_url(row["url"])
            except WebFetchError:
                continue
            safe.append({**row, "url": url, "discovery_engine": engine})
        return engine, safe, None if safe else "WEB_SEARCH_NO_RELEVANT_RESULTS"

    for start in range(0, len(jobs), 2):
        if loop.time() >= discovery_deadline:
            partial = True
            break
        batch = jobs[start:start + 2]
        attempted += len(batch)
        completed, pending = await _collect([search_one(job) for job in batch], deadline=discovery_deadline)
        partial |= pending
        for _, item in completed:
            if item is None:
                errors.append("WEB_SEARCH_FAILED")
                continue
            _, found, error = item
            if error:
                errors.append(error)
            for row in found:
                rows.setdefault(row["url"], row)
        if pending:
            break

    ranked = list(rows.values())[:MAX_REFERENCES] if resume else _varied(list(rows.values()))
    if not ranked:
        return {"action":"search", "status":"failed", "reason":
                "WEB_SEARCH_TIMEOUT" if partial else "WEB_SEARCH_NO_RELEVANT_RESULTS"
                if "WEB_SEARCH_NO_RELEVANT_RESULTS" in errors else "WEB_SEARCH_UNAVAILABLE",
                "sources":[], "references":[], "depth":"deep",
                "topic":topic[:240], "query":topic[:240], "discovered_at":discovered_at,
                "research_progress":{"search_calls_started":attempted, "stop_reason":"deadline" if partial else "no_results"}}
    references = [{"number": i, **row, "state":"found"} for i,row in enumerate(ranked,1)]
    nav._save_refs(session_id, references)
    sources = []
    failures = []
    cached_count = 0
    if resume:
        entries = {item.get("url"): item for item in (resume.get("entries") or [])
                   if isinstance(item, dict) and item.get("state") == "read"}
        # A fresh saved excerpt is not a new page fetch. The timestamp is
        # retained, and the provider is explicitly told about cached evidence.
        for index, row in enumerate(ranked[:MAX_READS]):
            entry = entries.get(row["url"])
            if (not entry or not entry.get("excerpt")
                    or not _fresh_timestamp(entry.get("fetched_at"), now)):
                continue
            sources.append({"url": row["url"], "title": entry.get("title", row["title"]),
                            "text": _clean(str(entry["excerpt"]), 950), "status": "opened",
                            "fetched_at": entry["fetched_at"], "cached_excerpt": True,
                            "retrieval": entry.get("retrieval", "workspace_checkpoint"),
                            "discovery_engine": "workspace_checkpoint"})
            references[index]["state"] = "read"
            cached_count += 1
    selected = [(i, row) for i, row in enumerate(ranked[:MAX_READS])
                if references[i]["state"] != "read"]

    async def read_one(row):
        try:
            page = await nav._read_page(row["url"])
            if page.get("status") != "opened" or nav._needs_browser(page):
                return None, "WEB_PAGE_CONTENT_UNAVAILABLE"
            url = _shareable_public_url(page.get("url") or row["url"])
            title = _clean(str(page.get("title") or row["title"]),180)
            body = _clean(str(page.get("text") or ""), MAX_EVIDENCE_CHARS)
            terms = _topic_terms(topic)
            corpus = _fold_search(title + " " + body + " " + urlsplit(url).path)
            if terms and sum(term in corpus for term in terms) < min(2,len(terms)):
                return None, "WEB_PAGE_OFF_TOPIC"
            return {"url":url, "title":title, "text":body, "status":"opened",
                    "fetched_at":str(page.get("fetched_at") or "")[:48],
                    "retrieval":page.get("retrieval") or "direct_http",
                    "discovery_engine":row["discovery_engine"]}, None
        except WebFetchError as exc:
            return None, exc.code
        except TimeoutError:
            return None, "WEB_PAGE_TIMEOUT"

    reads_started = 0
    for start in range(0,len(selected),2):
        if loop.time() >= deadline:
            partial = True
            break
        batch = selected[start:start+2]
        reads_started += len(batch)
        completed, pending = await _collect([read_one(row) for _, row in batch], deadline=deadline)
        partial |= pending
        for index, item in completed:
            page, error = item if item is not None else (None,"WEB_PAGE_FAILED")
            reference_index = batch[index][0]
            if error:
                failures.append({"number":reference_index+1,"reason":error})
            elif page:
                references[reference_index]["state"] = "read"
                if not any(s["url"] == page["url"] for s in sources):
                    sources.append(page)
        if pending:
            break
    nav._save_refs(session_id, references)
    return {
        "action":"search", "status":"opened" if sources else "results_only",
        "depth":"deep", "query":topic[:240], "topic":topic[:240],
        "discovered_at":discovered_at, "resume_eligible":len(topic)<=240,
        "resumed_checkpoint":bool(resume),
        "sources":sources, "references":references,
        "research_progress":{
            "search_calls_started":attempted, "reads_started":reads_started,
            "read_count":len(sources), "found_count":len(references),
            "cached_excerpt_count":cached_count,
            "partial":partial or bool(errors) or bool(failures) or not sources,
            "stop_reason":"deadline" if partial else "checkpoint_complete" if resume and not selected else "bounded_pass_complete",
            "search_failures":errors[:MAX_SEARCH_CALLS], "read_failures":failures[:MAX_READS],
            "cross_source_claims_verified":False,
        },
    }
