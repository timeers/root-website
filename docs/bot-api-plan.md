# Third-party bot HTTP API — Phase 1 (`/schedule`)

> **Status: proposal — not started.** Drafted 2026-09-04. Nothing here is implemented;
> no code, models, or migrations from this document exist yet. Line references point at
> `post-split` as of drafting and may drift.
>
> Line-numbered links assume this file lives in `docs/`. The eventual integrator-facing
> reference is a separate document (`docs/bot-api.md`), described under Rollout step 7.

## Context

External parties want to build their own Discord bots that do what the in-house `the_databot` bot does — set match times with `/schedule`, run faction drafts with `/draft`, and so on. Today the only way in is `POST /discord/interactions/` ([the_databot/urls.py:13](../the_databot/urls.py#L13)), which is Ed25519-signed by Discord and speaks Discord's interaction wire format exclusively. There is no way for anyone else to drive the site.

The obstacle is not the HTTP layer — it is that the scheduling *domain* logic is fused to the Discord *presentation* logic inside a single 7,526-line module. `_handle_schedule_confirm` reads a `custom_id` string, decides permissions, writes `Match.scheduled_time`, and builds an embed, all in one function. A second front end written naively against those models would silently reimplement the subtlest rules — when a poll closes vs. when a time is written, the AGREED parking rule, the supersede sweep — and drift.

We already have evidence this happens: [the_warroom/views.py:8405-8480](../the_warroom/views.py#L8405-L8480) is a web view that reschedules a match. It **duplicates** the write, uses a *different* permission check (`tournament.has_permission` instead of `Match.can_schedule`), skips the consensus flow entirely, and doesn't announce to the schedule channel. It shares exactly one function with the bot — lazily imported, with the comment *"Same sweep every /schedule write path does -- this one was missing."*

**Intended outcome:** a documented, token-authenticated JSON API for `/schedule` that a third-party bot can drive, sitting on a shared Discord-free service layer that the in-house bot also calls — so the two front ends structurally cannot disagree about who may schedule or what a vote means. The refactor that enables this is valuable on its own and ships first.

## Decisions

| | |
|---|---|
| **Identity** | Bot token + acting user. The bot authenticates with its own token and asserts the acting Discord user's snowflake per request. Every write goes through `Match.can_schedule(profile)` — a bot can never do more than that user clicking in Discord could. |
| **Token issuance** | One global token per application, approved by a site admin. Per-guild scoping is designed in as an unused hook, not built. |
| **Addressing** | Discord IDs only: `guild_id` + `channel_id`, exactly like the bot. No `match_id` addressing. |
| **Title fallback** | **Disabled for API callers.** Resolution by linked thread URL only. |
| **Consensus** | Full flow exposed — propose / confirm / reject / set. |
| **Scope** | `/schedule` only. `/draft` and the rest are out of scope. |

Why the title fallback is off: [`_match_for_thread`](../the_databot/discord_interactions.py#L682) falls back to matching a thread's *title* against `PlayerGroup.name`, and self-heals by writing `PlayerGroup.discord_thread` ([:743](../the_databot/discord_interactions.py#L743)). A third-party bot could use that to bind a thread it controls to a group it doesn't. API callers get a 404 pointing at the series edit page instead.

## Findings that shape the plan

1. **A stale comment misdescribes the consensus gate.** [`_consensus_required`](../the_databot/discord_interactions.py#L1099) claims `requires_schedule_confirmation()` also requires "players actually being permitted to schedule." It does not — [the_warroom/models.py:799-807](../the_warroom/models.py#L799-L807) says *"Deliberately does NOT consider recording_access."* The AGREED parking branch handles that case now. Fix the comment while moving the function; the API must expose `agreed` as a first-class state.

2. **`_finalize_proposal` already announces to the schedule channel** ([:1506](../the_databot/discord_interactions.py#L1506)), so the consensus path gets that for free. Only direct-write and clear need to arrange it.

3. **The direct-write path has no row lock.** `_handle_schedule_confirm` does a plain read-modify-write of `scheduled_time` ([:1714-1718](../the_databot/discord_interactions.py#L1714-L1718)), unlike `_finalize_proposal` which uses `select_for_update`. Pre-existing, small, and the new `set_time` service is the natural place to close it — but it is a behaviour change, so it gets its own commit and test.

4. **The API must look the acting user up, never create them.** [`ensure_profile_from_discord`](../the_databot/tasks.py#L142) does three things in order: match by verified `discord_id`, else claim an *unlinked* profile by username, else **create** one. Only the first is safe for a caller whose assertions nobody verified.

   - Step 2 is the spoofing vector: Discord verifies the username *it* sends, but a third-party bot merely asserts it. Passing `username=None` skips this branch entirely (it is guarded by `if cleaned:`).
   - Step 3 is the one the API must not reach at all: it would let any approved bot spray `Profile` rows for arbitrary snowflakes, none of them backed by a verified human.

   So the API does **not** call `ensure_profile_from_discord`. It does a plain lookup and refuses on a miss:

   ```python
   profile = Profile.objects.filter(discord_id=str(acting_discord_id)).first()
   if profile is None:
       return error(403, "acting_user_unknown", ...)   # "log in with Discord once"
   ```

   **Consequence: a third-party bot can only ever act as a user who has already logged into the site with Discord.** That is a materially stronger boundary than "spoofing buys nothing because `can_schedule` gates it" — the set of impersonable identities is bounded by people who chose to link their account, not by every snowflake in existence.

   This diverges from the in-house bot, which deliberately get-or-creates so it can remember a timezone even in an unlinked thread ([:1530-1536](../the_databot/discord_interactions.py#L1530-L1536)). Nothing is lost: that path (`_handle_schedule_unlinked`) writes nothing and only posts a Discord message, and is already out of API scope. Document the divergence in the code so nobody later "fixes" the API to match the bot.

---

## Part 1 — The refactor: `the_databot/services/scheduling.py`

**This is the heart of the plan and ships alone, first.** Every decision that determines *what happens to the database* moves into Discord-free functions. `discord_interactions.py` keeps only *what the user sees*.

Module docstring should state the contract in the style of [`time_parsing.py`](../the_databot/services/time_parsing.py)'s header:

> Discord-free scheduling domain logic, shared by the Discord interaction handlers and the third-party bot HTTP API. Nothing here imports a Discord payload, builds an embed, or returns a `JsonResponse`. Callers translate the returned result objects into their own wire format. This exists so the two front ends cannot drift about who may schedule, what a poll outcome means, or when a time is written.

### Result shape

```python
@dataclass(frozen=True)
class Result:
    ok: bool
    code: str | None = None      # machine-readable, stable — the API's error code
    message: str | None = None   # human-readable — what Discord shows today, unchanged
    data: dict = field(default_factory=dict)
```

`code` is the load-bearing addition: today every failure is prose (`"another time was confirmed for this match first"`). Discord handlers keep rendering `.message`, so user-visible copy is untouched — which is what makes this refactor testable against the existing suite.

### The functions

| Function | Kind | Source | Notes |
|---|---|---|---|
| `resolve_match_for_thread(*, guild_id, channel_id, channel_name=None, prefer, allow_title_fallback=True)` | refactor | `_match_for_thread` [:682](../the_databot/discord_interactions.py#L682), `_schedulable_matches` [:666](../the_databot/discord_interactions.py#L666) | Only behavioural change: wrap the title-fallback block [:707-743](../the_databot/discord_interactions.py#L707-L743), including the `link_group_thread` self-heal, in `if allow_title_fallback:`. Discord passes `True`; API passes `False`. The existing unlinked message at [:745-749](../the_databot/discord_interactions.py#L745-L749) already says what decision 4 needs — reuse it verbatim with `code="match_not_linked"`. |
| `set_match_time(*, match, actor, when, source)` | refactor + hardening | write body of `_handle_schedule_confirm` [:1691-1712](../the_databot/discord_interactions.py#L1691-L1712) | `can_schedule` → **re-fetch under `select_for_update`** (finding 3) → read `previous_time` **before** the write → `save(update_fields=["scheduled_time"])` → `_announce_schedule_to_channel` → `_cancel_open_proposals(match, "cancelled")`. The `update_fields` comment travels with the code: a bare `save()` re-runs `Match.save()`'s name/number derivation. |
| `clear_match_time(*, match, actor, source)` | refactor | `_handle_schedule_clear_confirm` [:2158](../the_databot/discord_interactions.py#L2158) | Guards in existing order: permission → `scheduled_time is None` → `code="not_scheduled"`. No schedule-channel announcement on clear (`_announce_schedule_to_channel` early-returns on `new_time is None`) — preserve that asymmetry, don't fix it here. |
| `consensus_required(match)` | move + doc fix | [:1099](../the_databot/discord_interactions.py#L1099), `_match_roster` [:776](../the_databot/discord_interactions.py#L776) | Fix the stale docstring (finding 1). |
| `open_proposal(*, match, when, proposer, guild_id, channel_id, source)` | refactor | model work of `_open_schedule_proposal` [:1758-1780](../the_databot/discord_interactions.py#L1758-L1780) | Create row, `roster.set()`, seed `confirmed_by` with the proposer **only if on the roster**, compute the `others` flag. The `post_schedule_proposal_task.apply_async(..., countdown=2)` call and `_schedule_proposal_data` render **stay** in `discord_interactions.py`. |
| `proposal_guards(*, proposal, guild_id, allow_agreed=False, allow_passed=False)` | refactor | payload-free core of `_proposal_for_click` [:1799-1866](../the_databot/discord_interactions.py#L1799-L1866) | See below — this one has a trap. |
| `record_response(*, proposal, match, responder, response, source)` | refactor + consolidation | `_handle_schedule_proposal_confirm` [:1893](../the_databot/discord_interactions.py#L1893) + `_reject` [:2063](../the_databot/discord_interactions.py#L2063) | See below. |
| `poll_outcome(proposal, match)` | refactor | decision core of `_resolve_match_poll` [:1931](../the_databot/discord_interactions.py#L1931) | **The pivotal one.** See below. |
| `finalize_proposal(proposal, actor=None)` | move, byte-for-byte | [:1449](../the_databot/discord_interactions.py#L1449) | Its ordering (authority → CAS → write → sweep) is documented as load-bearing. Move it whole, docstring included; add `code`s to the four failure returns, keep the prose in `.message`. |
| `cancel_open_proposals`, `announce_schedule_to_channel`, `resolve_clicker` | move | [:1400](../the_databot/discord_interactions.py#L1400), [:1424](../the_databot/discord_interactions.py#L1424), [:804](../the_databot/discord_interactions.py#L804) | Already payload-free. |
| `parse_time_input(*, text, tz_name)` | new, thin | wraps `parse_user_datetime` | Maps the `NEED_TIMEZONE` sentinel to `code="timezone_required"` so no caller string-compares the sentinel. |

Also move `_is_blacklisted` [:116](../the_databot/discord_interactions.py#L116) (or to a sibling `guards.py`) — the API needs the same 60s-cached blacklist check and must not import a private name across a package boundary.

#### `proposal_guards` — the trap

Its refusal branches don't merely refuse, they **retire** the proposal ([:1855-1864](../the_databot/discord_interactions.py#L1799-L1866)); the docstring explains a bare refusal left a live, fully-buttoned message. So `proposal_guards` performs the DB retire itself and signals it:

```python
Result(False, code="unschedulable", message=..., data={"retired": True, "retire_reason": "unschedulable"})
```

**The security property that must survive verbatim** ([:1848-1862](../the_databot/discord_interactions.py#L1848-L1862)): retire only when `proposal.guild_id` matches the requesting guild; otherwise refuse *without* retiring, so a cross-guild caller can't destroy a proposal it can't see. This matters more for the API than for Discord — a global-token bot could otherwise enumerate and kill proposals across every guild.

#### `record_response` — what to preserve

Unifies confirm and reject. The subtle parts, each of which has a load-bearing comment to carry across:
- Capture `already` **before** `confirmed_by.add()` — `add()` is idempotent, so afterwards a repeat click is indistinguishable from a first one ([:1916-1918](../the_databot/discord_interactions.py#L1916-L1918)).
- The early return is `already and not all_responded()`, **not** `already` alone ([:1924-1927](../the_databot/discord_interactions.py#L1924-L1927)) — once everyone else has confirmed, the seeded proposer pressing Confirm is *both* already-confirmed and the last confirmation owed; returning early would strand the proposal unscheduled.
- `rejected_by.remove(me)` — answering moves you between the columns.
- Reject's AGREED narrowing and moderator fallback ([:2074-2088](../the_databot/discord_interactions.py#L2063-L2102)).

API callers pass `username=None` to `resolve_clicker`, so `CLICKER_UNLINKED` is unreachable for them — they get `CLICKER_UNKNOWN`. **This is correct**: the unlinked message is an invitation to log in, and telling a third-party bot "this snowflake resembles an unlinked roster player" leaks who is on a roster. Document it.

#### `poll_outcome` — the single most important function

Extracts the decision core, preserving the existing order:

```
outcome = "pending"    if not proposal.all_responded()
        | "rejected"   if any rejected_by   -> LIVE-guarded UPDATE to REJECTED
        | "agreed"     if not match.can_schedule(proposal.proposed_by)
                            -> CAS OPEN -> AGREED; "closed" if the CAS lost
        | "scheduled"  -> finalize_proposal(proposal); "closed" with .message if not ok
```

Returns `data={"outcome", "proposal", "match", "declined", "pending", "failure"}`.

**Stays out** — all Discord-only: `_poll_notify_ids_from_payload` [:2019](../the_databot/discord_interactions.py#L2019) (parses snowflakes back out of a *rendered embed*), `_poll_author_from_payload`, every `_schedule_*_data` renderer, and `_notify_poll_closed` (its subscriber list comes from the embed, so it structurally cannot move).

`_resolve_match_poll` becomes: read `notify_ids` from the payload → call `poll_outcome` → switch on `outcome` to pick a renderer → fire `_notify_poll_closed` with `closed_by` still derived from the payload (preserving the "exclude whoever's click ended the poll" semantics).

Why this is pivotal: `poll_outcome` becomes the *only* place that decides `all_responded` = close and `all_confirmed`/`can_schedule(proposed_by)` = write-vs-park. The AGREED-parking rule is the subtlest in the feature and the one most likely to be silently reimplemented wrong in a new view.

### What stays in `discord_interactions.py`

Embed/component builders (`_schedule_confirm_data` [:968](../the_databot/discord_interactions.py#L968), `_schedule_proposal_data`, `_schedule_rejected_data`, `_schedule_agreed_data`, `_schedule_closed_data`, `_schedule_retire_response`, `_tz_region_data`); custom_id codecs and the `"g"` owner-lock-bypass convention; followup token sequencing (`post_interaction_followup_task ... countdown=2`); payload readers; `_ephemeral`; the timezone region/city picker; and the whole `_handle_schedule_unlinked` flow [:1626](../the_databot/discord_interactions.py#L1626) — out of API scope entirely, since it writes nothing and exists only to post a Discord message.

### Compatibility shims — how we prove this is behaviour-preserving

Every extraction leaves a same-named alias with the **same signature and return shape**:

```python
def _finalize_proposal(proposal, actor=None):
    r = scheduling.finalize_proposal(proposal, actor=actor)
    return r.ok, r.message
```

`_match_for_thread` keeps returning `(match, error)` with `allow_title_fallback=True` by default. `_cancel_open_proposals`, `_announce_schedule_to_channel`, `_consensus_required`, `_match_roster`, `_resolve_clicker`, `_schedulable_matches` keep their signatures.

**Acceptance criterion for Part 1: the entire existing suite passes unmodified.** [the_databot/tests.py](../the_databot/tests.py) is 10,474 lines with ~29 `Schedule*` classes covering confirm/reject/set/clear/supersede/invalidation in depth — a strong oracle.

Then repoint [the_warroom/views.py:8457](../the_warroom/views.py#L8457)'s lazy import at `the_databot.services.scheduling`. That module has no `the_warroom` import at module scope, so the documented circularity may be gone — check, and if so delete the workaround comment and import normally.

**Commit order** (smallest blast radius first, suite green at each step): `_schedulable_matches` → `_match_roster` → `_consensus_required` → `_announce_schedule_to_channel` → `_cancel_open_proposals` → `_finalize_proposal` → `_resolve_clicker` → `_match_for_thread` → `proposal_guards` → `poll_outcome` → `set_time`/`clear_time`/`open_proposal`/`record_response`. The first six are moves; the last five restructure control flow.

---

## Part 2 — Auth

### Where it lives: `the_databot/api/`

Not `the_warroom/api/` — all the reusable logic lives in `the_databot`, and `the_warroom` importing it at module scope is already known-circular ([views.py:8457](../the_warroom/views.py#L8457) documents the workaround). Not a new app — [the_databot/models.py:1-22](../the_databot/models.py#L1-L22) establishes the boundary as "everything here exists only because the bot exists," which a third-party bot API is. Layout mirrors `the_warroom/api/` (the house style): `authentication.py`, `permissions.py`, `throttling.py`, `serializers.py`, `views.py`, `urls.py`, `errors.py`.

### `BotApplication` — new model in `the_databot/models.py`

Next to `BotUsage`/`BotBlacklist`, the existing "bot's own bookkeeping" section.

`name`, `slug` (unique), `owner` FK→Profile (PROTECT), `description`, `contact`, `token_hash` (CharField(64), unique, indexed), `token_created`, `token_prefix` (CharField(12), plaintext first chars so an owner can identify the live token without it being usable), `status` (`PENDING`/`APPROVED`/`SUSPENDED`/`REVOKED`), `approved_by`, `approved_at`, `created_at`, `last_used_at`, plus the forward-compat hook: `guild_scope` (`ALL`/`LISTED`, default `ALL`) and `guilds` M2M→DiscordGuild.

Methods mirror `Profile`'s API-key pattern at [the_gatehouse/models.py:553-576](../the_gatehouse/models.py#L553-L576) — `hash_token()` staticmethod (SHA-256), `generate_token()` returning `secrets.token_urlsafe(32)` prefixed `rdb_` (greppable by secret scanners), raw value returned **once**.

```python
def may_act_in_guild(self, guild_id):
    if self.guild_scope == GuildScope.ALL:
        return True
    return self.guilds.filter(guild_id=str(guild_id)).exists()
```

Every view calls this from day one, so enabling per-guild scoping later is a data change plus a default flip — not an API change.

### `BotTokenAuthentication`

Modeled on [the_warroom/api/authentication.py](../the_warroom/api/authentication.py), inheriting its header-only rule **and its docstring rationale** (query strings hit access logs). Keyword `Bot-Token`, deliberately distinct from `Api-Key` so a leaked user key is useless here and vice versa; each authenticator returns `None` on a keyword it doesn't own, so they compose in the same chain.

Returns a `BotPrincipal` (application + acting profile + acting snowflake), **not** a Django `User` — this is the important divergence from `ProfileApiKeyAuthentication`, which returns `profile.user`. A bot has no user and must not inherit one. Give it `is_authenticated = True` and a `pk` property (`f"bot-{application.pk}"`) so DRF's throttling doesn't silently fall back to IP keying.

Note [settings.py:346](../django_project/settings.py#L346) sets **no `DEFAULT_PERMISSION_CLASSES`**, so DRF defaults to `AllowAny` — every bot view sets `permission_classes` explicitly.

### Acting user: `X-Acting-Discord-User: <snowflake>`

Header, not a body field: it applies uniformly to GET and POST, it's identity rather than payload, and it keeps request serializers about *what* is being done. Validated in the authentication class against `^\d{17,20}$` (reuse `the_gatehouse.models.validate_discord_snowflake`) so it fails before any view logic.

Resolved by **lookup only** — `Profile.objects.filter(discord_id=...)`, never `ensure_profile_from_discord`. A miss is `403 acting_user_unknown` with copy telling the integrator their user must log in with Discord once, mirroring the phrasing `_login_hint()` ([:843](../the_databot/discord_interactions.py#L843)) already uses. See finding 4 for the reasoning, and put it in the docstring so the API is never "fixed" to match the bot's get-or-create.

### Admin & registration

Admin (`the_databot/admin.py`, matching `BotBlacklistAdmin`'s action style): list/filter/search, `readonly_fields` for every token field, actions `approve` / `suspend` / `revoke` (revoke clears `token_hash`, killing the token immediately). **No token generation in admin** — the raw token must reach the *owner*, not an admin's clipboard.

Owner-facing pages in `the_gatehouse`, following [`generate_api_key`](../the_gatehouse/views.py#L149) exactly: create a `PENDING` application; generate a token only once `APPROVED`; surface the raw value via the same session read-and-clear PRG pattern with the same "copy it now, it will not be shown again" message. Regeneration rotates; `token_prefix` is how the owner tells which is live.

---

## Part 3 — Endpoints

Prefix `/api/bot/v1/`. All requests carry `Authorization: Bot-Token <t>` and `X-Acting-Discord-User: <snowflake>`.

**Error envelope** — every non-2xx:

```json
{ "error": { "code": "forbidden", "message": "You're not able to schedule this match.", "details": {} } }
```

`code` is stable and machine-readable (from `Result.code`); `message` is the same prose the in-house bot shows, so a third-party bot can relay it verbatim and stay consistent.

**Status codes:** 400 malformed input · 401 bad/missing token · 403 not approved, guild not permitted, blacklisted, **`acting_user_unknown`**, `can_schedule` refusal · 404 thread not linked, proposal not found · 409 proposal no longer active, superseded, expired · 422 `timezone_required` (well-formed but needs more info — distinct from 400 so integrators can branch) · 429 throttled.

`acting_user_unknown` fires in the authentication layer, before any resolution or parsing, so an integrator learns immediately rather than after a confusing partial success. Its `message` should carry the site login URL, as `_login_hint()` does.

| Endpoint | Purpose |
|---|---|
| `GET /threads/{guild_id}/{channel_id}/` | Inspect: `match`, `actor` (with `can_schedule`), `consensus_required`, `live_proposals`, and the acting user's stored `timezone` (lets a caller pre-empt the 422 round-trip). `?prefer=unscheduled\|scheduled`. |
| `POST /schedule/set/` | `{guild_id, channel_id, time \| time_utc, timezone?}`. Resolve → `can_schedule` → parse → `consensus_required` → branch to a direct write (`result: "scheduled"`) or an opened proposal (`result: "proposal_opened"`). |
| `POST /schedule/clear/` | `{guild_id, channel_id}`, resolved with `prefer="scheduled"` (the multi-game flip). 409 `not_scheduled` when there's nothing to remove. |
| `POST /proposals/{id}/confirm/` | `{guild_id}` — **required despite the id**, because it drives the guild-scope check in `proposal_guards`. `allow_agreed=False, allow_passed=False`. |
| `POST /proposals/{id}/reject/` | `{guild_id}`. `allow_agreed=True, allow_passed=True` — matching the existing handlers and their docstrings. |
| `POST /proposals/{id}/set/` | The AGREED → scheduled transition. `finalize_proposal(proposal, actor=acting_profile)` — **`actor=` is essential**: defaulting to the proposer would refuse every time, since they're a player who deliberately cannot schedule. Without this endpoint, AGREED is a dead end for API-only guilds. |
| `GET /proposals/{id}/?guild_id=` | Read state, so a bot can refresh its rendered message without casting a vote. Runs the guild-scope check but not the retire-on-refuse branches. |

Response shapes are driven by `poll_outcome`'s `outcome`: `pending` / `scheduled` / `agreed` (with `requires_moderator: true`) / `rejected` (with `declined`). A repeat confirm returns **200** with `already_recorded: true`, not an error — the in-house bot's equivalent is an ephemeral explanation, and that's the right semantics for an idempotent vote.

Every mutating response carries an `announcement` object (`text` using Discord `<t:…:F>` markup via `format_discord_timestamp`, plus a raw `timestamp`) so the calling bot renders the equivalent message itself, in its own voice. Roster entries for players who haven't linked Discord return `discord_id: null` — **not** the `profile-<pk>` stand-in `_proposal_entries` uses internally, so a bot can't mistake it for a snowflake.

The supersede race surfaces as `409 superseded` with the match's *actual current* `scheduled_time` in `details`, so the caller can immediately tell its user which time won.

`timezone` persistence mirrors the bot exactly, including the ordering ([:1600-1604](../the_databot/discord_interactions.py#L1600-L1604)): written only when an explicit timezone parsed *and* the whole operation succeeded, so a good timezone paired with an unreadable time isn't saved.

Deliberately **not** included: a `force_direct` consensus bypass. The in-house bot has no such thing, and adding it would be exactly the divergence this plan exists to prevent.

---

## Part 4 — Side effects

Three channels, three answers:

1. **Interaction followups never fire.** They need a Discord interaction token an API caller doesn't have. The information they carry is returned in `announcement` instead.

2. **`_announce_schedule_to_channel` stays on, unconditionally.** It posts to the *tournament's* configured schedule channel — a tournament-level announcement, not a bot-level one. How the time arrived is irrelevant to the organizer, and suppressing it would make the schedule channel silently incomplete. It is already self-guarding (`post_to_tournament_channel` refuses any channel it can't confirm belongs to the tournament's guild) and already no-ops when the time is unchanged. It also comes free on the consensus path via `finalize_proposal`, so suppressing it for API callers would mean adding a flag to `finalize_proposal` — reintroducing the divergence. Return `announcement_posted` so the caller can avoid double-posting.

3. **Proposal messages: post by default, opt out with `post_to_channel: false`.** A `ScheduleProposal` is a roster-wide artifact, not the calling bot's private state: the Confirm/Reject buttons and the 🔔 subscriber list live on that Discord message. If an API-opened proposal has no message, roster players who use the *in-house* bot cannot vote on it at all. Defaulting to posting keeps mixed-bot guilds working; a bot that renders its own UI can opt out. Verify `strip_schedule_proposal_messages_task` tolerates a blank `message_id` for the opted-out case, and cover it with a test.

4. **Known gap: 🔔 DMs on cross-front-end proposals.** The subscriber list lives *in the rendered embed*, not the DB ([:2019-2025](../the_databot/discord_interactions.py#L2019-L2025): *"a subscriber need not have a Profile, so an M2M could not hold them"*). A proposal opened via Discord and confirmed via API has no payload to read subscribers from, so they get no closing DM. **Accept and document for phase 1.** The proper fix — a `notify_discord_ids` JSONField on `ScheduleProposal`, populated at creation — is a schema change that also touches the LFG poll code, and is larger than the rest of this plan.

Conversely, `_cancel_open_proposals` fires regardless of caller, so an API write that supersedes a Discord-opened proposal *does* get that proposal's buttons stripped by our bot. That is exactly right, and is the main reason it must be called from every API write path.

---

## Part 5 — Security

| Threat | Mitigation |
|---|---|
| **Token leakage** | Header-only, never a query param. SHA-256 only in the DB, raw value shown once. Distinct `Bot-Token` keyword. `rdb_` prefix for secret scanners. Revoke clears the hash, killing the token instantly. |
| **Acting-user spoofing — the central threat** | Nothing cryptographic stops a bot claiming any snowflake, so the defence is layered. (1) **Lookup, never create** (finding 4): the bot can only name a user who has already logged into the site with Discord — every unlinked snowflake is a flat `403`, and no `Profile` row is ever created by an API call. (2) **The assertion buys nothing anyway**: every write goes through `Match.can_schedule(profile)`, so even a valid impersonation only reaches permissions that user already had. (3) `username=None` semantics are moot once creation is off, but stay documented so the claim-by-username branch is never reintroduced. (4) `may_act_in_guild` bounds blast radius when phase 2 flips it on. (5) The audit log makes impersonation detectable after the fact. (6) Admin approval means the owner is a known Profile. **State plainly in the integrator docs: a bot token can act as any *linked* user in any guild the bot can reach.** |
| **Guild scoping** | `may_act_in_guild(guild_id)` called on every endpoint from day one (a no-op under `ALL`). Independently, `_schedulable_matches(guild_id)` already scopes every match query to that guild's tournaments, and `proposal_guards` enforces the cross-guild retire split. **Additionally gate on `DiscordGuild.enabled_commands` containing `"schedule"`** ([the_gatehouse/models.py:181](../the_gatehouse/models.py#L181)) — that is the guild moderator's *existing* consent signal for this feature, so a guild that never enabled `/schedule` isn't reachable through a third-party bot either. Turns "global token" into "global across guilds that opted into scheduling," at zero UX cost. |
| **Blacklist bypass** | `BotBlacklist.is_blocked(user_id, guild_id)` on every request via the cached helper. Without it, a user blocked from the in-house bot could just switch bots. |
| **Replay** | Naturally idempotent or CAS-guarded throughout: `confirmed_by.add()` is idempotent, `finalize_proposal`'s CAS makes double-finalize impossible, `set_time` is last-write-wins under a row lock, and `_announce_schedule_to_channel` no-ops on an unchanged time. No nonce scheme needed; `Idempotency-Key` is the additive path if wanted later. |
| **Rate limiting** | Real gap: [settings.py:350-356](../django_project/settings.py#L350-L356) configures only `UserRateThrottle` at 1000/day and **no `AnonRateThrottle`**. Add a `ScopedRateThrottle` keyed on the application *and* a second scope on the acting user, so one bot can't burn its budget impersonating one person. Suggested `'bot': '600/hour'`, `'bot_user': '60/hour'`, **set per-view** so existing `/api/` endpoints are untouched. |
| **Audit logging** | Currently absent — `BotUsage` is counters only. New `BotApiAuditLog` model: application, acting snowflake, acting profile, guild/channel, endpoint, match, proposal, outcome code, previous/new time, timestamp; indexed on `(application, created_at)` and `(acting_discord_id, created_at)`. Written **only on mutating endpoints**, asynchronously via a Celery task following `record_bot_usage_task`'s fire-and-forget pattern. This is the only thing that makes spoofing detectable after the fact, so it is not optional. Add a pruning task alongside `cleanup_stale_schedule_proposals`. Fold `last_used_at` into the same task (only when staler than ~5 min) to avoid a write per request. |
| **Proposal-flood DoS** | Multiple OPEN proposals per match are legal by design, so a bot could open unlimited ones. Cap LIVE proposals per match (say 5) → 409 `too_many_proposals`, applied **in the service** so Discord gets the same guard. Flag as a deliberate behaviour change with its own test. |

---

## Part 6 — Verification

**Part 1 gate — the refactor:** run the full existing suite unmodified after every commit.
```
python manage.py test the_databot
```
~29 `Schedule*` classes exercise the paths that matter; `ScheduleWriteRuleTests`, `ScheduleProposalSupersedeTests` and `ScheduleProposalInvalidationTests` are the load-bearing ones. Any change required to an existing test means the extraction changed behaviour — stop and reconcile rather than editing the test.

**New: `the_databot/tests_scheduling_service.py`** — service-level, no Discord payloads, reusing `ScheduleFixtureMixin` ([tests.py:396](../the_databot/tests.py#L396)):
- `resolve_match_for_thread(allow_title_fallback=False)` refuses a title-only match that the `True` variant resolves — **and asserts `PlayerGroup.discord_thread` was NOT written**. This is the direct test of the title-fallback decision.
- `poll_outcome` returns each of `pending`/`rejected`/`agreed`/`scheduled`/`closed` from pure model state. These are the shared contract tests; the AGREED branch under MODERATORS-only `recording_access` is the important one.
- `finalize_proposal` CAS: finalize two competing proposals, second returns `code="superseded"`.
- `set_time` asserts the supersede sweep ran.

**New: `the_databot/tests_api.py`** — DRF `APIClient`, a new file rather than appending to a 10k-line module:
- Auth matrix: missing token 401, wrong keyword 401, PENDING/SUSPENDED 403, revoked 401, malformed snowflake 400, missing acting-user header 400. An `Api-Key` rejected on the bot API and vice versa.
- **Acting user must be linked:** a well-formed snowflake with no matching `Profile.discord_id` → 403 `acting_user_unknown`, **and `Profile.objects.count()` is unchanged** — the assertion that no API call can ever create a profile. Repeat for a snowflake whose *username* matches an unlinked profile: still 403, still no write, proving the claim-by-username branch is unreachable.
- Blacklisted user and guild → 403.
- `guild_scope=LISTED` with a non-listed guild → 403 (proves the forward-compat hook works before it's turned on).
- **Cross-guild:** a proposal in guild A, confirm asserting guild B → refused **and the proposal is NOT retired**.
- Unlinked thread → 404 `match_not_linked` with the same message the bot shows.
- `timezone_required` 422 for `"friday 8pm"` with no stored timezone; succeeds with `timezone`, with `time_utc`, and with a `<t:…>` paste.
- Full consensus round trip both ways: propose → confirm ×N → `scheduled`; propose → one reject → `rejected`.
- AGREED path end to end: MODERATORS-only tournament, all confirm via API → `requires_moderator: true` with `scheduled_time` still null; then `/set/` as a player → 403, as a moderator → `scheduled`.
- Idempotent repeat confirm → 200 `already_recorded: true`.
- Audit rows written for mutating calls, not for GETs.

**Cross-front-end interop tests — the ones that would catch drift.** Assert a proposal opened via Discord and confirmed via API reaches the same DB state as one confirmed via Discord, comparing `ScheduleProposal.status`, `Match.scheduled_time`, and the set of superseded siblings. Plus one white-box guard: patch `scheduling.poll_outcome` and assert *both* `_resolve_match_poll` and the API confirm view invoke it — the mechanical check against someone reimplementing outcome logic in a view later.

**Manual pilot:** enable the feature flag on staging, approve one application, and run a real match through set → propose → confirm → scheduled from a scratch bot, watching the audit log and the tournament schedule channel.

---

## Rollout

1. **Refactor** — `services/scheduling.py`, aliases left behind, docstring fix. Existing suite green, unmodified. Ship alone; independently valuable.
2. **Service tests** — pin the contract before a second consumer exists.
3. **Models + migration `0002`** — `BotApplication`, `BotApiAuditLog`, admin classes. No endpoints; safe to deploy.
4. **Registration UI** in `the_gatehouse`. Admins can approve, owners can generate; still nothing to call.
5. **The API, dark** — `the_databot/api/` mounted in `django_project/urls.py`, gated on `BOT_API_ENABLED` (default off in production). Widen the `SetLanguageMiddleware` prefix exemption ([the_gatehouse/middleware.py:20](../the_gatehouse/middleware.py#L20)) to cover `/api/bot/` alongside `/discord/interactions/`. Add throttle scopes. Add `tests_api.py`.
6. **Pilot** — enable the flag, approve one trusted integrator, watch the audit log and throttle counters.
7. **Publish** — `docs/bot-api.md`, changelog entry, open registration.

Documentation for integrators (`docs/bot-api.md`) must lead with the two things that will otherwise generate every support question: **the thread must be linked by a moderator on the series edit page** (with the 404 message quoted and the reason the title fallback is bot-only), and **what `agreed` means** — the state integrators are most likely to mishandle. Include the error-code table with suggested user-facing copy, the three ways to avoid `timezone_required`, and a `/v1/` versioning promise noting that `guild_id` is already required everywhere so per-guild scoping will not be a breaking change.

## Explicitly out of scope

`/draft` and every other command; `match_id` addressing; per-guild token grants (hook only); DB-backed poll subscribers; webhooks notifying bots of times set on the website. Each is accommodated by the design above.
