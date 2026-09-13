# FamilyWall wire contract

Status: **source-derived**. Produced by P0 task 00A on 2026-09-13 from the pinned
TypeScript client at `familywall-api@c85bb152115d3c41ab90322ef9dce732122ff4c0`.
No call in this document has been observed against the live service by this
project. See [calendar contract](calendar.md) for calendar semantics and
[research](../research.md) for the wider evidence base.

## Evidence levels

| Level | Meaning |
| --- | --- |
| `source-tested` | The pinned repository has an offline test asserting the request fields or parsed shape. It proves what that client sends and accepts, not what the server returns today. |
| `source-only` | Present in client code with no test. Request construction is readable; response shape is the author's assumption. |
| `pending-live` | Cannot be settled without an authorised FamilyWall test account. |

`source-tested` is the ceiling available offline. Nothing here may be labelled
live-verified until P2's opt-in probe runs.

## Transport

- Base: `POST https://api.familywall.com/api/<endpoint>`
- Body: `application/x-www-form-urlencoded; charset=UTF-8`
- Every request carries `partnerScope=Family`.
- Field names are namespaced per batched sub-call: `a00*` is the primary call,
  `a01*`–`a06*` are additional calls batched into the same request.

### Batching

One HTTP request may invoke several operations. The primary operation is the
endpoint path; extra operations are added as `aNNcall=<endpoint>` with their own
`aNN*` parameters. Each reply is keyed by the same `aNN` prefix. `accgetallfamily`
is the only batched call the port needs in v1.

### Envelope

Success: `{"a00": {"r": {"r": <result>}}}` — unwrap `a00.r.r`.
Failure: `{"a00": {"ex": <error>}}`, returned with **HTTP 200**. The error is
either a string or `{"ex": {"message": "..."}}`.

Evidence: `source-tested` (client.ts:616-648).

A Python port must therefore treat HTTP 200 as inconclusive and inspect the
envelope before deciding success. Six distinct failures must be separable:
non-2xx HTTP, HTML body (session expiry / login page), invalid JSON, envelope
missing `a00`, `a00.ex` present, and `a00.r.r` of an unexpected shape.

## Session handshake

| Step | Endpoint | Fields | Evidence |
| --- | --- | --- | --- |
| 1. Login | `log2in` | `a00identifier`, `a00password`, `a00generateAutologinToken=true`, `a01call=log2get`, `transactional=true` | `source-tested` (client.ts:651-684) |
| 2. Set web state | `webset` | `var=a`, `value=t` | `source-only` (client.ts:682-716) |
| 3. Get web state | `webget` | `var=a` | `source-only` (client.ts:682-716) |

Login returns a `Set-Cookie` containing `JSESSIONID`. Subsequent requests send
that value **twice**: as the `cookie` header and as a `tokencsrf` header
(client.ts:580-583). There is no separate CSRF token and no token rotation.

There is **no logout endpoint** in the reference client. Session teardown is
local only; the port cannot revoke an upstream session and must treat a
FamilyWall session as valid until it demonstrably fails.

### Deliberate divergences from the TypeScript client

1. The TS client rebuilds a cookie string containing hard-coded Google Analytics
   cookie values captured from a browser session. Those values are third-party
   analytics identifiers, not required credentials. **Do not port them**, and do
   not copy the literals into this repository. Start with `JSESSIONID` only and
   record (`pending-live`) whether anything else is required.
2. The TS client sends a constant `deviceId` and `modelType=WebFirebase` on
   account-state calls (client.ts:1191-1193). These are a fixed browser
   fingerprint. Port them only if a live probe shows the call fails without
   them; if so, generate a per-installation value rather than reusing theirs.
3. Login failure in the TS client retries once on a missing cookie and then
   returns a value. The Python client must raise a typed error instead.

## Account, family and calendar identity

Four identifiers must never be conflated:

| Identifier | Origin | Use |
| --- | --- | --- |
| MCP user ID | This project's database | OAuth subject; owns an encrypted credential record |
| FamilyWall account ID | `accgetallfamily` profiles (`accountId`) | Identifies a person inside a family |
| Family ID | `a00.r.r.family_id` | Scopes family data |
| Calendar ID | Derived as `calendar/{family_id}` | Only accepted calendar reference |

The calendar ID derivation is `source-only` (family.ts:34,57). It is a string
template, not a value the server returned, so P2 must confirm it against a live
account before any calendar tool ships.

### Discovery: `accgetallfamily`

Primary call plus six batched sub-calls (client.ts:1181-1202), `source-tested`:

| Key | Sub-call | Contents |
| --- | --- | --- |
| `a00` | `accgetallfamily` | Family payload: `family_id`, `members[]`, cover media |
| `a01` | `prfgetProfiles` | Profiles keyed by account ID |
| `a02` | `famlistfamily` | Family list |
| `a03` | `settingsgetperfamily` | Per-family settings |
| `a04` | `famshowincominginvite` | Incoming invitations |
| `a05` | `imthreadlist` (`a05isLoggedFamily=false`) | Message threads |
| `a06` | `accgetstate` (device fields, `a06timezone`) | Account/premium state |

Only `a00` (family identity) and `a01` (member names) are needed for v1. The
port should issue a reduced batch rather than copying all six.

## Family scoping — resolved P0 question

**No list, task or calendar-range request in the reference client carries a
family identifier.** `taskgettasklists` sends `partnerScope=Family` and nothing
else (lists.test.ts:105-108). Scope is therefore a property of the authenticated
session, not a request parameter.

Consequences, which are binding on later phases:

1. There is no verified way to select a family per request, and no field may be
   invented. `a02 famlistfamily` can enumerate families but no
   "switch active family" call is evidenced anywhere in the client.
2. V1 must operate strictly within whatever family the session resolves to, and
   must surface that family's identity in its tool output so the user can see
   which household answered.
3. If discovery returns more than one family, the tools must **refuse** rather
   than guess, and report that multi-family support is unimplemented. Silently
   acting on the first family is prohibited.
4. Whether the session's family is stable, and whether `webset` influences it,
   is `pending-live`.

`evtsync` accepts `a01withAllFamilies=true` (client.ts:770-781), which hints at
cross-family calendar reads. That is a lead for P8, not a v1 contract.

## Shopping lists

### `taskgettasklists` — list summaries

Request: `partnerScope=Family` only. Evidence: `source-tested`
(lists.test.ts:105-108).

Response: either a bare array, or an object under one of `lists`, `taskLists`,
`results` (client.ts:255-270).

Per list, accepted aliases (client.ts:162-206):

| Field | Aliases | Required |
| --- | --- | --- |
| id | `metaId`, `taskListId`, `listId`, `id` | yes |
| name | `name`, `title` | yes |
| type | `type`, `taskListType` → `shopping` / `todo` / `other` | no |
| itemCount | `itemCount` (number or numeric string) | no |
| checkedCount | `checkedCount` (number or numeric string) | no |
| color | `color` | no |

Unknown list types must be preserved as received, not coerced to `other`.

### `tasklist` — list detail

Request: `a00listId`. Evidence: `source-tested` (lists.test.ts:205-209).

Response: object containing `items` / `tasks` / `listItems`, or a bare item
array (client.ts:872-921).

Per item (client.ts:208-253):

| Field | Aliases | Notes |
| --- | --- | --- |
| id | `metaId`, `taskId`, `id` | required |
| text | `text`, `name`, `title` | required |
| completed | `complete`, `completed`, `checked`, `isChecked` | boolean **or** the string `"true"` |
| quantity | `quantity` | string or number; free text, not a parsed unit |
| authorId | `accountId` | FamilyWall account ID |
| categories | `categories` | objects with `.name`, or plain strings |
| creationDate | `creationDate` | ISO datetime |

### `taskcreate` — add an item

Request: `a00taskListId`, `a00text`, optional `a00quantity`. Evidence:
`source-tested` (list-items.test.ts:29-35).

Response: a bare string ID **or** an object carrying item metadata
(client.ts:968-997).

Quantity is passed through verbatim. No unit parsing, normalisation or
conversion is in the contract and none may be added.

### `taskmark` — check or uncheck an item

Request: `a00taskId`, `a00complete` as the string `"true"` or `"false"`. No list
ID is sent. Evidence: `source-tested` (list-items.test.ts:82-88).

Response: the TypeScript client **discards** it (client.ts:1006-1018), so the
acknowledgement shape is `source-only` at best. The Python service must re-read
the list to confirm state rather than trusting the response.

Because no list ID is sent, item ownership cannot be enforced by the request.
The service layer must verify the item belongs to an accessible list *before*
calling `taskmark`.

### `taskcreatelist` — create a list

Request: `a00name`, `a00taskListType` in `SHOPPING` / `TODO` / `OTHER`.
Evidence: `source-tested` (lists.test.ts:132-161). Deferred to P8; recorded here
so the type vocabulary is not re-derived later.

### Not available

Item edit and item delete have **no endpoint evidence** in the reference client.
They are excluded from v1 and from cleanup procedures; test items created during
acceptance are removed through the FamilyWall UI.

## Mutation acknowledgement

Both writes (`taskcreate`, `taskmark`) may return only an ID, or nothing
meaningful. Neither proves durable state. Every write therefore has three
outcomes the port must represent distinctly:

- **confirmed** — write acknowledged *and* a subsequent read shows the expected state;
- **acknowledged** — upstream returned success but readback did not confirm;
- **unknown** — the response was lost, timed out, or was unparseable.

`unknown` must never be retried automatically: `taskcreate` is not idempotent
and a retry adds a second item. This is what the P3 operation-receipt design
exists to handle.

## Deferred surfaces

Present in the reference client, out of v1 scope, contracts recorded for P8:
`imthreadlist`, `immessagelist2`, `imsend`, attachment download (with credential
isolation across redirects and a read-time size limit), and
`webgetWebSocketUrl`. Meals, recipes, categories and ingredient transfer have
endpoint names only and no contracts at all.

## Open questions (`pending-live`)

1. Is `JSESSIONID` alone sufficient, or are further cookies/headers required?
2. Are `webset`/`webget` required for list and calendar calls, or login-only?
3. What does the session's family resolve to for an account in several families,
   and can it be changed?
4. Is the derived `calendar/{family_id}` calendar ID accepted?
5. What does `taskmark` actually return?
6. How does an expired session present — HTTP 401, an HTML login page, or an
   `a00.ex` envelope?
7. Do list responses truncate, and is there any continuation mechanism?
