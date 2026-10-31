# ADR-0028 — Operator Web People Settings capability

**Status:** ACCEPTED  
**Date:** 2026-10-31

## Context

The Web/PWA is the current private conversation surface, but Step 1 requires two
administrative views in the same product: People/Family Settings and the safe
Living Console projection. Reusing the raw admin token in browser storage would
collapse the existing trust boundary. Reusing the ordinary `web` pairing code
and allowing the client to request a stronger role would create a privilege
escalation path.

The profile bootstrap file is also packaged read-only in the cloud container.
Mutating that file would lose changes on redeploy and would make Git seed data a
second mutable authority beside SQLite/libSQL.

## Decision

Introduce a bounded browser role named `operator`.

```text
amora pair --role operator
→ separate one-time challenge
→ browser.v3 signed operator credential
→ /v1/operator/* + GET /v1/operations/state only
```

The ordinary `amora pair` challenge cannot be redeemed as operator. The
operator challenge uses a distinct cache file, three-minute TTL and five-attempt
limit. The resulting credential expires after 12 hours and is signed by the
admin secret; rotation of the admin secret revokes it without exposing that
secret to the browser.

`operator` is not an alias for `admin` and cannot call conversation, memory,
world, cognition journal, raw event stream, ActionEngine or general state routes.
The normal `web` role cannot call operator routes.

## People/Family persistence

`config/people_profiles.json` remains a versioned bootstrap catalog. Once a
profile exists in canonical storage, operator edits in SQLite/libSQL win across
restart/redeploy. The bootstrap must not silently reapply removed structural
relations.

People Settings writes through typed Core services/storage:

- `PersonProfileSeed` for stable operator-owned profile fields;
- temporal Relationship Graph revisions for family topology;
- `platform_identity_bindings` for private platform identities.

Public profile/family JSON and private binding JSON are separate export/import
contracts. Raw external actor IDs are masked in list views and never appear in
public export, health or ordinary diagnostics. Private export/reveal requires
operator capability.

Confirmed imports validate before commit and apply transactionally. Destructive
relationship changes and family-authority changes require explicit confirmation.

## Web UI scope

Chat, People and Console are UI/UX surfaces and are not part of the current
avatar/body freeze. This ADR does not reopen body rigging, body animation,
brain-to-body realization, visual perception, OCR/screen mapping or V1.2
self-image/aesthetic work.

## Consequences

- a browser can manage People/Family without receiving the master admin token;
- operator access can be locally locked and naturally expires;
- profile changes survive cloud compute replacement when storage is remote;
- multiple WhatsApp/Discord/Web accounts remain bindings of one canonical PERSON;
- the future messaging adapters can consume the same canonical identity model;
- the Web Console reuses `/v1/operations/state` instead of exposing a new raw
  debugging backdoor.

## Rejected alternatives

### Put the admin token in localStorage

Rejected: it turns a bounded Web surface into full administrative access.

### Let `client=operator` upgrade an ordinary pairing code

Rejected: role selection by the browser must not raise privilege.

### Edit `config/people_profiles.json` inside the running container

Rejected: the file is bootstrap/read-only and redeploy would erase runtime
changes.

### Store phone/Discord IDs directly in public people profiles

Rejected: platform identifiers are private credentials/identity metadata and a
person may own multiple accounts.
