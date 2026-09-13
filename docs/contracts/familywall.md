# FamilyWall wire contract

Status: **source-derived, with a live-verified core**. Produced by P0 task 00A on
2026-09-13 from the pinned TypeScript client at
`familywall-api@c85bb152115d3c41ab90322ef9dce732122ff4c0`, then corrected by the
02P read-only live probe on 2026-09-13. Where the two disagree, the
[live verification section](#live-verification--02p-probe-2026-09-13) wins. See [calendar contract](calendar.md) for calendar semantics and
[research](../research.md) for the wider evidence base.

## Evidence levels

| Level | Meaning |
| --- | --- |
| `source-tested` | The pinned repository has an offline test asserting the request fields or parsed shape. It proves what that client sends and accepts, not what the server returns today. |
| `source-only` | Present in client code with no test. Request construction is readable; response shape is the author's assumption. |
| `pending-live` | Cannot be settled without an authorised FamilyWall test account. |
| `live-verified` | Observed against the live service by this project's own probe, with the date. Read paths only; no write endpoint has been observed. |

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

## Live verification — 02P probe, 2026-09-13

A throwaway, read-only probe ran against `https://api.familywall.com/api` with a
real account supplied outside Git. No write endpoint was called. Only key names,
structural shapes, enum values and date semantics were retained; every free-text
value, name, title and identifier was hashed before display and none of it is
recorded here. The script was discarded.

Items below are `live-verified (2026-09-13)`. They **supersede** the
source-derived statements they contradict.

### Corrections to the source-derived contract

| Source-derived claim | Live result |
| --- | --- |
| Failure is `a00.ex` | Two distinct failure envelopes exist: `a00.un.un` and `a00.ex.ex` (below) |
| Send `JSESSIONID` as the `tokencsrf` header | A `tokencsrf` header is **mandatory**; login returns its own `tokenCsrf` value, which is what the port sends |
| List types are `SHOPPING` / `TODO` / `OTHER` | Read responses use `SHOPPING_LIST` / `TODOS` / `OTHER` |
| Items carry a `quantity` field | No quantity-like key appears on any read item across four lists (85 items). Treat quantity as unproven |
| A browser `User-Agent` is required | **Withdrawn.** An authenticated call succeeds with no `User-Agent`, with the httpx default, and with a browser string. The earlier `502` was a missing `tokencsrf`, misattributed |

### Authentication — settled

- `log2in` succeeds with the documented fields and sets three cookies:
  `AWSALB`, `AWSALBCORS` and `JSESSIONID`. Only `JSESSIONID` is required; the two
  load-balancer cookies can be dropped and calls still succeed.
- The login result `a00.r.r` carries `accountId`, `tokenCsrf` (32 chars),
  `genericAutologinToken`, `hasFamily` and terms flags.
- **Every authenticated call must send a `tokencsrf` request header.** Omitting it
  returns `a00.un.un` with `message: "Wrong anti csrf token=null"`. Both the
  login `tokenCsrf` and the `JSESSIONID` value are accepted; the port sends the
  login `tokenCsrf`, because that is the value the server names.
- `webset` / `webget` are **not** required. Discovery, list and calendar calls all
  succeed immediately after `log2in` without them. Do not port them.
- The static analytics cookies and the constant browser `deviceId` are confirmed
  unnecessary. Do not port them.

### Envelope — settled

Success: `{"aNN": {"r": {"r": <result>}, "cn": "<endpoint>"}}`.

Failure comes in two shapes, both with **HTTP 200** and both carrying
`FiZClassId` (a server-side class code as a string) and a `message`:

| Shape | Observed `FiZClassId` | Observed meaning |
| --- | --- | --- |
| `aNN.un.un` | `501` | Not permitted for this session — missing/expired auth, missing CSRF header |
| `aNN.un.un` | `502` | Request rejected — malformed identifier, unparseable date |
| `aNN.ex.ex` | `3` | Application error — e.g. `bad password` on `log2in` |

`aNN.cn` echoes the endpoint name and is a useful error label. `message` is a
server diagnostic string; it may be logged as an endpoint-scoped label but must
never be returned verbatim to a model or user.

An **expired or invalid session presents as HTTP 200 JSON**, not 401 and not an
HTML login page: `a00.un.un` / `501` / `"Api <endpoint> is not allowed by ruleset
NOAUTHENT"`. The HTML-body failure mode was not observed; keep the branch, but the
`NOAUTHENT` envelope is the reauthentication trigger the client must detect.

### Discovery — settled

`accgetallfamily` with `a01call=prfgetProfiles` and no device fields succeeds.
`a00.r.r` is a **single object, not an array**, with keys:

`coverDefault`, `coverMedias`, `coverUri`, `deletedProfiles`, `family_id`,
`invitations`, `isFirstFamily`, `medias`, `members`, `metaId`, `name`,
`pictureDefault`, `wallCounter`.

- `family_id` is a bare numeric string; `metaId` is the prefixed form
  `family/<family_id>`.
- `members[]` entries carry `accountId`, `firstName`, `name`, `role`, `right`,
  `color`, `timeZone` (IANA, e.g. `Australia/Sydney`), `familyId` and
  `isloggedaccount`. `isloggedaccount` identifies the authenticated member.
- `a01` returns profiles keyed by account ID.

Because `a00.r.r` is one object, this endpoint gives **no way to enumerate several
families**, which reinforces the single-family rule rather than weakening it. The
probe account has exactly one family, so the multi-family branch is still
`pending-live`: the port must keep refusing rather than guessing, and must not
assume the payload would become an array.

### Lists — settled

`taskgettasklists` (only `partnerScope=Family`) returns `a00.r.r` as a **bare
array**. Per list, the observed keys are:

`accountId`, `alexa`, `bestMoment`, `clientOpId`, `color`, `comments`,
`completedHidden`, `creationDate`, `emoji`, `familyId`, `lastAction`,
`lastActionAuthor`, `lastActionDate`, `medias`, `metaId`, `moodMap`,
`moodStarShortcut`, `name`, `remainingTaskNumber`, `rights`, `sharedMemberIds`,
`sharedToAll`, `sortingIndex`, `taskCategoriesHidden`, `taskListType`,
`taskSorting`, `totalTaskNumber` (system lists add `systemId` and
`pinSortingIndex`).

- Identity is `metaId`, of the form `taskList/<id>`. No `taskListId` / `listId` /
  `id` alias was returned.
- `taskListType` observed values: `SHOPPING_LIST`, `TODOS`, `OTHER`. The
  source-derived `SHOPPING` / `TODO` vocabulary belongs to the **write** path
  (`taskcreatelist`) and must not be used to interpret reads.
- Counts are `totalTaskNumber` and `remainingTaskNumber` (remaining, not checked).
  `itemCount` / `checkedCount` were not returned.
- Booleans arrive as the **strings** `"true"` / `"false"` throughout.

`tasklist` with `a00listId=<metaId>` returns `a00.r.r` as a **bare array** of
items with keys:

`accountId`, `assignee`, `assigneeIds`, `bestMoment`, `categories`, `clientOpId`,
`comments`, `complete`, `completedDate`, `creationDate`, `description`,
`editable`, `familyId`, `lastAction`, `lastActionAuthor`, `lastActionDate`,
`medias`, `metaId`, `modifDate`, `moodMap`, `moodStarShortcut`, `recurrency`,
`recurrencyDeletedOccurence`, `reminder`, `sortingIndex`, `taskCategoryId`,
`taskId`, `taskListId`, `text`, `toAll`.

- Identity is `metaId` of the form `task/<id>`; `taskId` is the bare numeric form
  and `taskListId` is the owning list's `metaId`. **`taskListId` on the item is
  what lets the service verify membership before `taskmark`.**
- `complete` is the string `"false"` / `"true"`.
- `categories` are objects with `name` and a `system` string flag.
- **No quantity field was returned on any item.** Quantity remains a write-path
  parameter with no evidenced read-back, so a quantity a tool writes may not be
  observable. This must be stated as a limitation rather than assumed to work.

A malformed list identifier returns `a00.un.un` / `502` /
`"No enum constant com.jeronimo.fiz.api.common.MetaIdTypeEnum.<prefix>"`, which
confirms identifiers are prefix-typed and are validated server-side.

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

## Open questions

Answered by the 02P probe on 2026-09-13, now `live-verified`:

1. ~~Is `JSESSIONID` alone sufficient?~~ `JSESSIONID` **plus** a `tokencsrf`
   header. Nothing else.
2. ~~Are `webset`/`webget` required?~~ No.
3. ~~What does `taskmark` return?~~ Still unanswered — deferred, see below.
4. ~~How does an expired session present?~~ HTTP 200 with
   `a00.un.un` / `501` / `NOAUTHENT`.
5. ~~Do list responses truncate?~~ No cap observed: a list with 780 items
   returned in full, and a 1095-day calendar window returned 1115 events. No
   continuation mechanism exists or appears necessary. The port still bounds its
   own request windows and output size.

Still `pending-live`:

1. **What does `taskmark` actually return, and what does `taskcreate` return?**
   Both are writes and the 02P probe was read-only. They are answered by the P3
   controlled live acceptance in a disposable test list, not by another probe.
2. **Is `quantity` writable and readable at all?** No read evidence exists. P3
   must write a quantity and re-read it before any tool advertises the field.
3. **What does discovery return for an account in more than one family?** The
   probe account has one. `a00.r.r` is an object, so the multi-family shape is
   unknown. The single-family rule stands and must fail closed on anything
   unexpected.
4. **Does the session's family ever change, and can it be selected?** No
   selector was found and none was tested.
