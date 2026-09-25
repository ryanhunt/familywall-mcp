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
| A browser `User-Agent` is required | **Withdrawn.** An authenticated call succeeds with no `User-Agent`, with the httpx default, and with a browser string. The earlier `502` in the first probe was transient load-balancer flakiness, not a protocol error |

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

### Transient upstream failures — load balancer

Observed independently twice on 2026-09-14: upstream returns **HTTP 502 Bad Gateway
with a non-JSON body** (plain HTML error page). This is transient infrastructure
flakiness at the API's load balancer, not an application error, and retrying
seconds later succeeds both times. A client must treat both non-2xx status and
non-JSON body as real, recurring conditions and handle them as transport errors,
not as protocol violations. Do not silently retry writes on 502; they may be
partially delivered and replay protection relies on the receipt state machine.
Read operations may be retried; write operations must not.

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

### Item edit and delete — evidence found 2026-09-14

The reference TypeScript client has no item edit or delete call, which earlier
led this contract to state that no such endpoint exists. That was wrong. The web
client declares `taskdelete(["taskId"])` and
`taskupdate(["taskId","text","dueDate","assignee","reminder"])`.

`taskdelete` is now **`live-verified` (2026-09-14)**: 13 test items created
during the write check were deleted by `taskdelete` with `a00taskId`, all 13
returned a success envelope, and a verification re-read found none remaining.
Deletion is immediate and there is no trash or undo, so it must never be offered
as a v1 tool without an explicit confirmation design.

`taskupdate` remains unexercised; the web app itself uses `taskupdate2`, which
is live-verified as a patch (see
[Probe A2](#probe-a2--list-assignment-and-the-2-endpoints-2026-09-25)).
Cleanup of test items no longer requires the FamilyWall UI.

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

## Live write verification — 2026-09-14, and a blocking finding

A controlled live write check ran against a disposable list. It answered the
write-path questions and produced **a finding that blocks the headline v1 use
case**.

### `taskcreate` ignores the list identifier

Four field spellings were tried against a list named `Claude`
(`taskListType: OTHER`):

| Variant | Field | Value |
| --- | --- | --- |
| A | `a00taskListId` | prefixed `taskList/<id>` (what this client sends) |
| B | `a00taskListId` | bare numeric id |
| C | `a00listId` | prefixed `taskList/<id>` |
| D | `a00taskList` | prefixed `taskList/<id>` |

**All four returned HTTP 200 with a success envelope, created a task, and put it
in the family's default `TODOS` list — never in the requested list.** The
response's own `taskListId` field reports the default list's id in every case,
and `categories` comes back as the system category `SYS-CAT-TODOS`.

The target list's item count never changed. There is therefore **no evidenced
way to create an item in a chosen list** with this endpoint as currently
understood. `a00taskListId` appears in the reference TypeScript client
(`list-items.test.ts:29-35`) as a `source-tested` request field, but that test
asserts only what the client *sends*; the live service ignores it.

Consequences, binding until discovery resolves them:

1. **"Add bread to the shopping list" cannot be delivered.** An add lands in the
   default TODOS list regardless of the list the user names, which is a silent
   wrong-destination write — worse than a refusal.
2. `add_list_item` must **not** ship claiming it can target a list. Either the
   tool is withheld, or it states plainly that items go to the default list.
3. The list-selection logic in `services/lists.py` is correct and well tested,
   but it currently selects a list the write then ignores.
4. Further discovery is needed: another endpoint, an extra required field, or a
   separate "move to list" call. The reference client is exhausted as a source —
   this needs observation of the real web app's network traffic.

### `taskcreate` response shape — answered

It returns a **full task object**, not a bare string id:

`accountId`, `assignee[]`, `assigneeIds[]`, `bestMoment`, `categories[]`,
`comments[]`, `complete`, `creationDate`, `editable`, `familyId`, `lastAction`
(`CREATED`), `lastActionAuthor`, `lastActionDate`, `medias[]`, `metaId`,
`modifDate`, `moodMap`, `moodStarShortcut`, `recurrency`,
`recurrencyDeletedOccurence`, `reminder`, `sortingIndex`, `taskId`,
`taskListId`, `text`, `toAll`.

`metaId` and `taskId` are both `task/<id>`. **`taskListId` reports where the item
actually went**, which is what exposed the finding above — a client that reads it
back can at least detect the wrong destination.

### `quantity` is silently dropped — answered

`a00quantity=2 boxes` was sent with a create. The response contains **no
quantity-like key at all**, matching the read-side finding of no quantity across
85 items. Quantity is accepted by the request, ignored by the service, and
unreadable. **No tool may advertise quantity**; passing one must either be
refused or reported as not stored.

### `taskmark` — effect verified, response shape still unobserved

Superseded by the 2026-09-14 live write check. `taskmark` **was** exercised once
the create-then-move flow worked: checking an item and then unchecking it both
took effect, confirmed by reading the list back each time, and an explicit
`false` was honoured rather than treated as a no-op.

Its **returned payload was never inspected**, so the response shape remains
unknown. That is not a gap worth closing: the service must re-read the list to
confirm state regardless, because the reference client discards this response and
no acknowledgement from it could be trusted on its own.

## Endpoint signatures from the web client — 2026-09-14, `live-verified`

The FamilyWall web app's own JavaScript bundle is served publicly as a static
asset at `https://www.familywall.com/generated/public/js/startupmodule.js`. It
declares each endpoint's parameter list literally. No credentials were used and
no request was made to the API to obtain this.

Verified fragments, quoted from the bundle:

| Endpoint | Declared parameters |
| --- | --- |
| `taskcreate` | `["text","dueDate","assignee","reminder"]` |
| `taskmove` | `["taskId","taskListId","prevTaskId","taskCategoryId"]` |
| `taskmark` | `["taskId","complete","completedDateForTesting"]` |
| `taskupdate` | `["taskId","text","dueDate","assignee","reminder"]` |
| `taskdelete` | `["taskId"]` |
| `tasklist` | `["listId"]` |
| `taskcreatelist` | `[{encode:...}]` |
| `taskcreate2` | `[{encode:...},"picture"]` |

### This explains the blocking finding

**`taskcreate` has no list parameter at all.** It was never ignoring our field —
the parameter does not exist in the endpoint's schema, which is why all four
spellings behaved identically and every item landed in the default list.

The web client creates a task in a chosen list in **two steps**:

1. `taskcreate` with the content — the task is created in the default list;
2. `taskmove` with `taskId` and the destination `taskListId` — the task is then
   placed in the intended list. `prevTaskId` orders it and `taskCategoryId`
   places it within a category.

Observed call sites in the bundle confirm the shape, including
`taskmove({taskId: ..., prevTaskId: ..., taskCategoryId: ...})` and a
category-header variant passing `taskCategoryId` with the `$empty` sentinel.

**The consequence for this project is that an add is not atomic.** A successful
`taskcreate` followed by a failed `taskmove` leaves the item sitting in the
default list — a partial write that is visible to the family. The three-state
write machine must represent that case explicitly rather than reporting a clean
success or a clean failure. It is a distinct outcome from both `acknowledged`
and `unknown`, and it must not be auto-retried: `taskcreate` is not idempotent,
so retrying the pair creates a second item.

### Two corrections to the earlier contract

1. **`quantity` is not a parameter of `taskcreate`.** The earlier live check
   showed it silently dropped; the declared signature now explains why. This is
   settled: quantity cannot be sent, stored or read, and no tool may offer it.
2. **Item delete and item edit DO exist.** The "Not available" section below is
   wrong: `taskdelete(["taskId"])` and `taskupdate(["taskId","text","dueDate",
   "assignee","reminder"])` are both declared. They were absent from the
   reference TypeScript client, not from the API. Neither has been exercised
   live, so both are `source-only` at the web-client level until tested — but
   the claim that no delete endpoint exists is withdrawn, and test-item cleanup
   no longer necessarily requires the UI.

`taskmark`'s third parameter, `completedDateForTesting`, is not used by this
project.

## Probe A2 — list assignment and the `…2` endpoints (2026-09-25)

Captured from the FamilyWall web app's own requests with the account owner
signed in, then checked with scripted calls. Only key names, value shapes and
masked IDs were recorded. Four disposable items were created, and all were
deleted with `taskdelete`.

**The web app uses `taskcreate2` and `taskupdate2`.** It does not use the
`taskcreate`/`taskupdate` pair declared in its bundle. It also batches requests:
the create travelled as sub-call `a01` behind a `taskgettasksuggestionswcat`
request, with `a01`-prefixed fields.

| Endpoint | Web form | Evidence |
| --- | --- | --- |
| `taskcreate2` | `taskListId`, `text`, `assignee=$empty`, `taskCategoryId=""`, `dueDate=$empty`, `picture=$empty` | live-verified |
| `taskupdate2` | `taskId` (`task/…`), `text`, `description`, `taskCategoryId`, `dueDate` (UTC instant or `$empty`), `recurrency`, `recurrencyInterval`, `byDay`, `byMonthDay`, `recurrencyEndDate`, `reminder.reminderType`/`reminderUnit`/`reminderValue`, `toAll`, `assignee.N`, `taskListId`, `picture` | live-verified |

**Assignment encodings.**

| Selection | Sent | Read back |
| --- | --- | --- |
| Nobody (web quick-add) | `taskcreate2` with `assignee=$empty` | `toAll:"false"`, `assigneeIds: []` |
| Two named members | `toAll=false`, `assignee.0`, `assignee.1` | those two IDs, in order |
| Everyone ("Assigned to everyone") | `toAll=true` **and** `assignee.N` for every member | `toAll:"true"`, and `assigneeIds` lists **every** member, in a different order |
| `taskcreate` as `add_list_item` sends it (`a00text` only) | — | default list, `toAll:"true"`, every member assigned |

For tasks, unlike events, "everyone" reads back with the full member list, so
compare assignees as a set.

**`taskupdate2` patches.** A scripted call with only `partnerScope`, `taskId`,
`toAll=false` and `assignee.0` changed the assignment and nothing else. `text`,
`description`, `dueDate`, the reminder and the list were all unchanged. The
brief 09 risk (an assignee change clearing a due date) does not occur on this
endpoint. The legacy `taskupdate` remains unexercised and should not be used.

**`taskmove` keeps the assignment**, and also the description, due date and
reminder.

**`taskcreate2` creates directly in a chosen list.** A scripted call with a
non-default list's `taskListId`, `toAll=false` and `assignee.0` created the item
in that list with exactly that assignee, in one call. `taskcreate` still has no
list parameter (the 2026-09-14 finding stands for that endpoint), but
`taskcreate2` makes the non-atomic create-then-move of ADR 0002 avoidable.

**Reads.** `dueDate` and `description` appear on task objects once they are set.
Earlier samples lacked them only because they were unset. The reminder reads
back as `{reminderUnit, reminderType, reminderValue, localId}`, with
`reminderType:"NONE"` when there is none.

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

1. ~~What does `taskcreate` return?~~ Answered 2026-09-14: a full task object,
   including the `taskListId` it actually used.
2. ~~Is `quantity` writable and readable?~~ Answered 2026-09-14: it is silently
   dropped. Not writable, not readable.
3. ~~How does one create an item in a chosen list?~~ Answered 2026-09-14:
   `taskcreate` followed by `taskmove`. Both endpoints live-verified; two-step
   write is not atomic and must be represented as a distinct outcome.
4. **What does `taskmark` return?** Its effect is live-verified (2026-09-14:
   check and uncheck both took effect, confirmed by readback), but its returned
   payload was never inspected. Low value to close, because the service re-reads
   the list to confirm state regardless.
5. **What does discovery return for an account in more than one family?** The
   probe account has one. `a00.r.r` is an object, so the multi-family shape is
   unknown. The single-family rule stands and must fail closed on anything
   unexpected.
6. **Does the session's family ever change, and can it be selected?** No
   selector was found and none was tested.
