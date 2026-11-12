# FinPilot — REST API Specification

**Status:** Planning document, updated as the project develops · **Date:** 2026-11-05 · **Incorporates:** OD-1 to OD-5, OD-7, OD-8, OD-10, OD-16, OD-17, assumptions #4, #5, and #10, and the language decision
**Role:** Proposed contract for web and future mobile clients. Not implemented. At build time, FastAPI's generated OpenAPI document becomes the machine-readable source, and it must stay consistent with this document.

---

## 1. Conventions

### 1.1 Base and versioning

- **Base path:** `/api/v1`
- **Additive changes** (new fields, new endpoints) don't change the version. Clients must ignore unknown fields.
- **Breaking changes** need `/api/v2`. Because mobile apps can't be force-updated, `v1` must stay supported for a deprecation period once mobile ships.

### 1.2 Authentication

| Client | Credential | CSRF |
|---|---|---|
| Web | `__Host-fp_session` cookie | `X-CSRF-Token` required on unsafe methods |
| Mobile (future) | `Authorization: Bearer <token>` | Not applicable |

### 1.3 Money

```json
{ "amount": "1250.50", "currency": "TRY" }
```

Always a canonical decimal string: ASCII digits, `.` as the decimal mark, no thousands separators, whatever the user's language. JSON numbers are rejected. Decimals beyond the currency's exponent return 422. Locale-aware parsing happens on the client (`FINANCIAL_RULES.md` M7 in §2), so the API is never ambiguous.

### 1.4 Dates

- **Instants:** RFC 3339, such as `2026-11-05T14:30:00Z`.
- **Business dates:** `YYYY-MM-DD`.
- **Months:** `YYYY-MM`.

### 1.5 Pagination, sorting, filtering

Keyset (cursor) pagination:

```
GET /transactions?limit=50&sort=-effective_date&cursor=eyJ2Ijo...
```

```json
{
  "items": [],
  "next_cursor": "eyJ2Ijo...",
  "has_more": true
}
```

- `limit`: default 50, maximum 200.
- `sort`: field from an allowlist per endpoint; `-` prefix for descending. A stable `id` tiebreak is always added.
- `cursor`: opaque and signed, and bound to the filters and sort used. A mismatch returns 400.
- Filters use named query parameters; multiple values repeat the parameter (`account_id=a&account_id=b`).

Keyset is used because results stay stable while data is being inserted, and it stays fast at depth. Total counts come from report summaries, not from each list call.

### 1.6 Idempotency

`Idempotency-Key: <uuid>` is **required** on endpoints marked **[I]**. Behavior is in `FINANCIAL_RULES.md` §14.1.

| Case | Result |
|---|---|
| New key | Normal processing |
| Same key, same request | Original status and body, with header `Idempotent-Replayed: true` |
| Same key, different request | 422 `idempotency_key_reused` |
| Missing key | 400 `idempotency_key_required` |

### 1.7 Concurrency

Mutable resources return a `version` field and an `ETag` header. Updates need `If-Match: <version>`.

| Case | Result |
|---|---|
| Header missing | 428 `precondition_required` |
| Version stale | 412 `version_conflict`, with the current representation |
| Illegal state transition | 409 `invalid_state` |

### 1.8 Errors

RFC 9457 `application/problem+json`, with a stable machine-readable `code`:

```json
{
  "type": "https://finpilot.example/errors/split_sum_mismatch",
  "title": "Shares do not add up to the expense amount",
  "status": 422,
  "code": "split_sum_mismatch",
  "detail": "Shares total 95.00 EUR but the expense is 100.00 EUR.",
  "request_id": "req_01J...",
  "params": { "shares_total": "95.00", "expense_total": "100.00", "currency": "EUR" },
  "errors": [
    { "field": "shares", "code": "sum_mismatch", "params": { "expected": "100.00" } }
  ]
}
```

**Language:** clients display a translated message from `code` and `params`. `title` and `detail` are always English, for developers and logs. CI fails if any error `code` lacks a translation in `en`, `ar`, or `tr`. An unknown code in an older mobile app falls back to a generic translated message.

| Status | Use |
|---|---|
| 400 | Malformed request or missing required header |
| 401 | Not authenticated |
| 403 | Authenticated and the resource is known to them, but the action is not allowed |
| 404 | Doesn't exist, **or the caller may not know it exists** |
| 409 | Valid request in conflict with current state |
| 412, 428 | Version preconditions |
| 413 | Export exceeds limits |
| 422 | Validation or business-rule failure |
| 429 | Rate limited or quota exceeded; includes `Retry-After` for rate limits, and `quota` and `limit` params for quotas |
| 503 | Dependency unavailable, such as the FX provider, where no fallback exists; or maintenance and read-only mode |

**Resource hiding:** requests about a trip you're not a member of, or anyone else's account, return **404**, never 403, so IDs can't be probed.

### 1.9 Authorization labels

| Label | Meaning |
|---|---|
| **Public** | No authentication |
| **User** | Any authenticated user, on their own data |
| **Member** | Active trip member |
| **Member+** | Active or former member, read-only |
| **Admin** | Trip owner or admin |
| **Owner** | Trip owner |
| **Party** | Specific named party; see the endpoint |

The full matrix is in `SECURITY.md` §4.

---

## 2. Authentication

**Google is the only way to authenticate.** There are no register, login, password, reset, or verification endpoints, and there won't be.

**Status:** everything below is built, except the mobile route. Automated tests use a fake Google. **A real development sign-in was verified end to end in A2.4.** Every account response is `Cache-Control: no-store`.

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /auth/google/start?locale=&return_to=` | Public, rate-limited | Web. Stores a single-use flow, sets the browser-binding cookie, and redirects to Google. `locale` (`en`, `ar`, `tr`) sets a new account's language. `return_to` is a local path such as `/en/dashboard`; anything else is 422 `invalid_return_path` |
| `GET /auth/google/callback` | Public, rate-limited, needs the binding cookie | Web. Checks state and browser, exchanges the code, verifies the ID token, creates or finds the user, issues a session, and redirects to the web app |
| `POST /auth/google/mobile` | Public | **Future.** Native clients. Body: `id_token`, `nonce`, `device_label`. Returns a bearer token once |

**Callback results.** Every outcome is a `303` redirect to the web app, with `Cache-Control: no-store`, and the binding cookie cleared. **No token, code, or Google error text is ever in a URL.**

| Situation | Redirect |
|---|---|
| Known Google account, or a new one with a verified email | Session and CSRF cookies set, then `{web_origin}{return_path}`, by default `/{locale}` |
| Cancelled at Google | `/{locale}?error=cancelled` |
| Google email not verified, or absent | `/{locale}?error=email_not_verified` |
| Account suspended or deleted | `/{locale}?error=account_unavailable` |
| Google unreachable, or sign-in not configured | `/{locale}?error=google_unavailable` |
| An unexpected server failure, such as the database being down | `/{locale}?error=unreachable` |
| Anything else: bad, expired, or reused state; wrong browser; missing code; any ID token check failing; too many attempts (429) | `/{locale}?error=failed`. Which check failed is logged and audited, never sent |

Both routes are page loads, so every failure above ends on the public landing page, never on raw JSON. The only exception is a malformed request the web app can't send (422 for a bad `locale` or `return_to`). `/{locale}` shows only these fixed codes, translated, and removes them from the address bar. When `locale` isn't known (no sign-in attempt to read it from), the `fp_locale` cookie is used, then English. **There is no standalone sign-in page:** `/{locale}/sign-in` only redirects to `/{locale}`, keeping validated `error`, `notice`, and `next` values.

---

## 3. Users and Sessions

**Built in A2.3:** the `/auth/...` routes in the first table. **Planned:** the rest.

**Changes need CSRF:** every `POST`, `PATCH`, and `DELETE` with a session cookie must send an allowed `Origin` (or `Referer`), and `X-CSRF-Token` set to the value of the `fp_csrf` cookie (`SECURITY.md` §3). Otherwise 403 `csrf_failed`.

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /auth/me` | Active user | Profile and preferences: `id`, `email`, `display_name`, `locale`, `preferred_currency`, `time_zone`, `status`, `created_at`. No session or Google data |
| `PATCH /auth/me` | Active user, CSRF | Any of `display_name` (1–100 characters, no control or bidirectional-override characters), `locale` (`en`, `ar`, `tr`), `preferred_currency` (three capital letters, or null), `time_zone` (a real IANA zone). **Any other field, including `email`, is 422** |
| `POST /auth/logout` | Optional session; CSRF when there is one | Revokes this session and clears the cookies. Always 204, so it's safe to repeat |
| `GET /auth/sessions` | Active user | This user's live sessions, newest first: `id`, `current`, `device_label` (such as "Firefox on Windows"), `created_at`, `last_seen_at`, `expires_at` |
| `DELETE /auth/sessions/{id}` | Active user, CSRF | Revokes one of the caller's own sessions. **Another user's session is 404,** so its existence isn't revealed |
| `DELETE /auth/sessions` | Active user, CSRF | Revokes every other session |

**Auth errors:** 401 `not_authenticated` for a missing, invalid, expired, or revoked session, with no detail. 403 `account_unavailable` for a suspended account, which also ends that session.

**"Recent sign-in required"** means the current session came from a Google sign-in by the same `sub` within 15 minutes; Google can't prove a password was re-entered (`SECURITY.md` §2.3). Proposed, for the owner to confirm.

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /me/data-export` | User | **Built.** The person's own data as a JSON attachment, synchronously. 5 an hour. `X-Export-Complete` is `false` if a section was cut, and the file names it. Allowed while deletion is pending. No re-authentication |
| `GET /me/deletion-request` | User | **Built.** Status, what blocks deletion, warnings, and whether the sign-in is recent |
| `POST /me/deletion-request` | User | **Built.** Body: `confirm_email`, `acknowledge_retention: true`. Moves the account to `deletion_pending`; erases nothing. **403 `recent_sign_in_required`**, **409 `deletion_already_requested`**, **422 `confirmation_mismatch`**. Track 1. Recent sign-in and typed confirmation required. **409 `deletion_blocked`** with blocking trips while balances or pending items are open [OD-3]. Returns impact summary and date |
| `DELETE /me/deletion-request` | User | **Built.** Cancel. **409 `deletion_not_requested`** if nothing is pending |
| `POST /me/personal-data-erasure` | User | **[proposed: OD-18]** Recent sign-in and typed confirmation required. Erases accounts, ledger, budgets, links, and private audit events. Never blocked by trip balances. Trip membership continues |
| `POST /privacy-requests` | Public | **[proposed: OD-18]** Track 2. Records a legal request, with its deadline, for human review. **Never returns an automatic refusal.** Rate-limited |
| `GET /me/privacy-requests` | User | Status of own requests |
| `GET /me/notifications` | User | Inbox, paginated |
| `POST /me/notifications/{id}/read` | User | Mark read |
| `GET /me/pending-actions` | User | Count and summary of approvals, confirmations, and link reviews |

---

## 4. Financial Accounts

**Built in A3.1**, as below. `POST /accounts` requires `Idempotency-Key`. Account names are 1 to 100 characters, trimmed, with no control or bidirectional-formatting characters, and unique among a user's active accounts (409 `account_name_taken`). Other codes: 422 `unsupported_currency`, `invalid_amount`, `currency_mismatch`, `date_in_future`; 428 `precondition_required`; 412 `version_conflict`. Every response is `Cache-Control: no-store`.

`GET /currencies` (User) lists the currencies new records may use, with their exponents, for the web app's amount inputs.

| Method & path | Auth | Notes |
|---|---|---|
| `GET /accounts` | User | Filter: `status`. Each item includes a derived balance |
| `POST /accounts` | User [I] | Optional opening balance posts an `opening_balance` transaction atomically |
| `GET /accounts/{id}` | User | |
| `PATCH /accounts/{id}` | User | Name and description only. If-Match |
| `POST /accounts/{id}/archive` | User | 409 `balance_not_zero` otherwise |
| `POST /accounts/{id}/unarchive` | User | |
| `DELETE /accounts/{id}` | User | 409 `account_has_entries` if any exist |

**Account response**

```json
{
  "id": "…",
  "name": "Ziraat Checking",
  "type": "bank",
  "currency": "TRY",
  "balance": { "amount": "12450.75", "currency": "TRY" },
  "status": "active",
  "opened_on": "2026-01-01",
  "version": 3
}
```

There is **no way to set a balance directly**; it's always derived.

**Built in A3.2:**

| Method & path | Auth | Notes |
|---|---|---|
| `GET /accounts/totals` | User | Active accounts' balances **per currency**, with the number of accounts. Never one total across currencies |
| `GET /accounts/{id}/activity?limit=` | User | Newest first, each amount signed from this account's side, with the other account of a transfer. `limit` 1 to 100, and `has_more` |
| `GET /accounts/{id}/adjustment-preview?target=&currency=` | User | The current balance, the target, the difference, and its direction, worked out by the backend |
| `POST /accounts/{id}/adjustments` | User [I] | Body: `target_balance`, `expected_balance` (the balance shown), optional `reason`. 409 `balance_changed` if the balance moved; 422 `no_change`; 409 `account_archived` |
| `GET /transfers/preview` | User | Query: `from_account_id`, `to_account_id`, `amount`, `currency`, optional `received_amount` and `received_currency`. Returns the `implied_rate` across currencies (six decimals, display only) |
| `POST /transfers` | User [I] | Body: `from_account_id`, `to_account_id`, `amount`, `received_amount` (required across currencies; must equal `amount` within one), optional `effective_date` (not in the future) and `description`. Codes: `same_account`, `received_amount_required`, `amounts_differ`, `amount_not_positive`, `account_archived`. Returns both accounts with their new balances |

Archiving still needs a zero balance (409 `balance_not_zero`): the web app explains this and offers a transfer or an adjustment to zero.

**Built in A4:** the account response adds `system_key` (`cash`, `bank`, or null) and `is_default`; `name` is null for the automatic Cash and Bank until renamed. `POST /accounts/{id}/make-default` (User) sets the account Quick Add uses; 409 `account_archived` for an archived one.

---

## 4a. Recurring Finances [Built in M5]

**As built** (everything below needs a session; changes need CSRF; creates and confirmations need `Idempotency-Key`; edits need If-Match):

| Method & path | Notes |
|---|---|
| `GET /recurring/rules` | Brings occurrences up to date, then lists rules with their next pending occurrence |
| `POST /recurring/rules` [I] | `kind` (`income`, `expense`, `debt_payment`), `name`, `amount` (null = variable), `currency`, `account_id`, `category_id` or `debt_id`, `frequency` (`weekly`, `biweekly`, `monthly`, `yearly`), `window_start_day`/`window_end_day`, `month`, `start_date`, `end_date`, `total_occurrences`, `reminders`, `remind_days_before`. Codes: `invalid_window`, `category_kind_mismatch`, `category_archived`, `account_archived`, `currency_mismatch` |
| `PATCH /recurring/rules/{id}` | Forward only (R7). Kind, frequency, start date and debt are fixed |
| `POST /recurring/rules/{id}/pause` · `/resume` · `/end` | If-Match. 409 `invalid_state` for the wrong starting status |
| `GET /recurring/occurrences?from=&to=&rule_id=` | Each with `status` (stored) and `state` (`expected`, `pending`, `overdue`, `confirmed`, `skipped`), expected and actual amounts, `sequence`/`total`, `remind` |
| `GET /recurring/upcoming?limit=` | Home's list: nearest pending occurrences with reminders on, not rested today |
| `POST /recurring/occurrences/{id}/confirm` [I] | `amount` (actual), optional `account_id`, `effective_date`, `category_id`, `description`, `conversion` (another currency), `account_amount` (debts across currencies). Returns the occurrence and the transaction. A repeat key replays; otherwise 409 `invalid_state` once answered |
| `POST /recurring/occurrences/{id}/not-yet` · `/skip` | Not yet records nothing and rests the reminder until tomorrow; skip closes this one. 409 once answered |

## 4b. Debts [Built in M5]

| Method & path | Notes |
|---|---|
| `GET /debts` | Each with `original`, `remaining` and `repaid` (derived from the ledger), `settled` |
| `POST /debts` [I] | `direction` (`i_owe`, `owed_to_me`), `counterparty`, `amount` (the debt's currency), optional `start_date`, `notes`, `account_id` (money moved now; omit if it existed before FinPilot) and `account_amount` (across currencies) |
| `GET /debts/{id}` | Plus its history (start and repayments) |
| `POST /debts/{id}/payments` [I] | `account_id`, `amount` (debt's currency), `account_amount` (across currencies), `effective_date`, `description`. Codes: `exceeds_remaining` (with `remaining`), `debt_settled`, `account_amount_required`, `amounts_differ` |
| `POST /debts/{id}/archive` · `/unarchive` | If-Match. Archive needs a settled debt (409 `debt_not_settled`) |

Voiding a debt's start while repayments stand: 409 `debt_has_payments`. Transactions carry `debt` (`id`, `counterparty`, `direction`, `currency`) and `debt_amount`.

**Planned contract (from the original specification):**

| Method & path | Auth | Notes |
|---|---|---|
| `GET /recurring/rules` · `POST` · `PATCH /{id}` · `POST /{id}/pause` · `/resume` · `/end` | User | Edits apply forward only (`FINANCIAL_RULES.md` R7). `POST` [I] |
| `GET /recurring/occurrences?from=&to=&status=` | User | Generates due occurrences lazily, then lists them, each with a derived `expected`, `pending`, `overdue`, `confirmed`, or `skipped` |
| `POST /recurring/occurrences/{id}/confirm` | User [I] | Body: actual `amount`, `effective_date`, `account_id`. Posts one transaction; a repeat replays; 409 `invalid_state` if skipped |
| `POST /recurring/occurrences/{id}/skip` | User | 409 `invalid_state` if confirmed |
| `GET /recurring/reminders` | User | Due, overdue, and soon-due occurrences, one per occurrence |

---

## 5. Transactions

**Built in A4**, for `income` and `expense`. Where this differs from the planned table further down, the built version is the contract. Every response is `no-store`.

| Method & path | Auth | Notes |
|---|---|---|
| `GET /setup` | User | `completed`, `needs_currency`, `preferred_currency`, `default_account_id` |
| `POST /setup` | User | Body `{currency?}`. Idempotent: seeds categories, creates Cash and Bank at zero for a user with no accounts, sets the default account. 409 `currency_required` when no currency is saved or given |
| `POST /transactions` | User [I] | Body: `type` (`income` or `expense`), `account_id`, `amount`, `category_id`, optional `effective_date` (default today in the user's zone; never in the future), `description` (≤200), `notes` (≤2000). Codes: 422 `currency_mismatch` (with `account_currency`), `amount_not_positive`, `category_kind_mismatch`, `date_in_future`; 409 `category_archived`, `account_archived` |
| `GET /transactions` | User | Filters: `type`, `account_id`, `category_id`, `from`, `to`, `q` (literal, description and notes), `include_voided`. Keyset paging: `limit` (1 to 100) and opaque `cursor`; returns `{items, next_cursor}`. 400 `invalid_cursor` |
| `GET /transactions/{id}` | User | With `changes` (created, corrected, voided, with times) and an ETag |
| `PATCH /transactions/{id}` | User | If-Match. A correction: any of `amount`, `account_id`, `category_id`, `effective_date`, `description`, `notes`. A change to the first four reverses the live entry and posts a replacement; text-only changes touch no journal. 409 `not_correctable` (not income or expense), `invalid_state` (voided); 412 `version_conflict` |
| `POST /transactions/{id}/void` | User | If-Match. Reverses the live entry; the document stays, `status: reversed`, shown crossed out in history |
| `GET /summary?month=YYYY-MM` | User | Per currency: `expenses`, `income`, `transactions` (posted income and expense), `top_categories` (5). Transfers, adjustments and opening balances are excluded. Never one total across currencies |

Not built yet: `refund`, `occurred_at` editing, `original_amount`, sorting other than newest first, `amount_min`/`amount_max`, and `quick-add-context` (its job is done by `last_used_at` on categories and the default account from `/setup`).

**Planned contract (from the original specification):**

| Method & path | Auth | Notes |
|---|---|---|
| `GET /transactions` | User | Filters below |
| `POST /transactions` | User [I] | Income, expense, refund, adjustment |
| `POST /transfers` | User [I] | Atomic; same or cross-currency |
| `GET /transactions/{id}` | User | Includes correction history |
| `POST /transactions/{id}/correct` | User [I] | If-Match; reversal + replacement |
| `POST /transactions/{id}/void` | User [I] | If-Match; reversal only |
| `GET /transactions/quick-add-context` | User | Recent accounts and categories, defaults |

`POST /transactions` doesn't accept `trip_*` types. Those are created only through `POST /links`, so cash can't reach Trip Clearing without a link.

**Filters:** `from`, `to`, `account_id`, `category_id`, `type`, `currency`, `status`, `q`, `amount_min`, `amount_max`.
**Sort:** `effective_date`, `occurred_at`, `amount`, `description`.

**`POST /transactions`** (only `type`, `amount`, `account_id`, and `category_id` are required):

```json
{
  "type": "expense",
  "account_id": "…",
  "amount": { "amount": "450.00", "currency": "TRY" },
  "category_id": "…",
  "occurred_at": "2026-11-05T12:30:00Z",
  "effective_date": "2026-11-05",
  "description": "Lunch",
  "notes": null,
  "original_amount": { "amount": "13.00", "currency": "USD" }
}
```

`amount.currency` must equal the account currency; otherwise 422 `currency_account_mismatch`.

**Negative balances are allowed.** A valid request is never rejected because of the resulting balance. The 201 response then includes `"warnings": ["negative_balance"]`, and this applies to transfers too. No fee or interest entry is ever created.

Amounts are always positive. `type: "adjustment"` also requires `direction: "increase"` or `"decrease"`, and optionally a reason (owner decision, 2026-11-06). A negative opening balance is sent the same way when creating an account.

**`POST /transfers`**

```json
{
  "from_account_id": "…",
  "to_account_id": "…",
  "from_amount": { "amount": "3500.00", "currency": "TRY" },
  "to_amount": { "amount": "100.00", "currency": "EUR" },
  "effective_date": "2026-11-05",
  "description": "Savings"
}
```

For same-currency accounts, the amounts must match. Responds with the implied rate.

**Corrections and void:** `opening_balance` is correctable. `type` can't change.

---

## 6. Categories

**Built in A4**, per user (see `DATABASE.md` §3.3):

| Method & path | Auth | Notes |
|---|---|---|
| `GET /categories?include_archived=` | User | The user's categories in display order, each with `last_used_at` |
| `POST /categories` | User | `kind`, `name`, `icon`, `color`. 409 `category_name_taken`; 422 `invalid_icon`, `invalid_color` |
| `PATCH /categories/{id}` | User | If-Match. `name` (null resets a default to its translated name), `icon`, `color`, `status` (`active` or `archived`). `kind` never changes |

There is no delete: an archived category leaves the pickers and stays on past entries.

**Planned contract (from the original specification):**

| Method & path | Auth | Notes |
|---|---|---|
| `GET /categories` | User | System and own custom. Filters: `kind`, `status` |
| `POST /categories` | User | |
| `PATCH /categories/{id}` | User | Custom only |
| `POST /categories/{id}/archive` | User | Custom only |
| `DELETE /categories/{id}` | User | 409 if referenced |

---

## 7. Budgets and Analytics

**As built in M6** (session required; writes need CSRF and are rate limited; reads run in one REPEATABLE READ snapshot):

- `GET /budgets?month=YYYY-MM` → `{month, budgets[], archived[]}`; each budget has `category` (null = overall), `limit`, `spent`, `remaining`, `percent` (text, one decimal), `state`, `alert_percent`, `effective_from`, `not_counted[]`.
- `PUT /budgets` `{category_id|null, limit: Money, from_month, alert_percent=80}` creates or changes a limit from that month. `422 category_kind_mismatch` for an income category.
- `POST /budgets/end` `{category_id|null, currency, from_month}` archives from that month (`404` if none is in effect).
- `GET /budgets/detail?currency&month&category_id` → `{budget, history[], months[6], transactions[]}`.
- `GET /insights?from&to&all_history&currency&account_id` → `{currency, currencies[], date_from, date_to, totals, previous, categories[], monthly[12], daily[], grain}`; `grain` is day (≤ 62 days), week (≤ 400) or month.


| Method & path | Auth | Notes |
|---|---|---|
| `GET /budgets?month=2026-11` | User | Effective limits and progress |
| `PUT /budgets` | User | Sets a limit from a given month; creates a new effective-dated row |
| `DELETE /budgets/{id}?from_month=` | User | Ends a budget from a month forward |
| `GET /analytics/dashboard?month=` | User | All dashboard data |
| `GET /analytics/spending-trend?months=12&currency=` | User | |
| `GET /analytics/spending-by-category?from=&to=&currency=&trip_allocation=` | User | `include` (default), `exclude`, `only` |
| `GET /analytics/cash-flow?from=&to=&currency=` | User | |
| `GET /analytics/cash-vs-allocated?from=&to=&currency=` | User | Compares actual cash flow with allocated spending |

**Budget item**

```json
{
  "id": "…",
  "category": { "id": "…", "name": "Food and drinks" },
  "limit": { "amount": "5000.00", "currency": "TRY" },
  "spent": { "amount": "3210.50", "currency": "TRY" },
  "remaining": { "amount": "1789.50", "currency": "TRY" },
  "progress_pct": "64.21",
  "overspent": false,
  "included": {
    "non_trip": { "amount": "3110.50", "currency": "TRY" },
    "trip_allocation": { "amount": "100.00", "currency": "TRY" }
  },
  "uncounted_other_currency": [ { "amount": "45.00", "currency": "EUR" } ],
  "excluded_custom_category_trip_shares": false
}
```

**`spent` uses allocated trip shares, not amounts paid on behalf of others** (OD-10), in the budget's currency only (OD-7). Budgets take no `trip_allocation` filter. Rules are in `FINANCIAL_RULES.md` §6.

**Dashboard:** balances are returned **per currency**. `approx_total` is optional, and is always labeled with its rate date and source:

```json
{
  "balances_by_currency": [ { "amount": "12450.75", "currency": "TRY" } ],
  "approx_total": {
    "amount": { "amount": "12400.00", "currency": "TRY" },
    "is_estimate": true,
    "rates_as_of": "2026-10-30",
    "rate_source": "ECB via Frankfurter"
  },
  "by_currency": [
    {
      "currency": "TRY",
      "income": { "amount": "30000.00", "currency": "TRY" },
      "non_trip_spending": { "amount": "18000.00", "currency": "TRY" },
      "trip_allocation": { "amount": "0.00", "currency": "TRY" },
      "personal_spending": { "amount": "18000.00", "currency": "TRY" },
      "trip_cash_out": { "amount": "0.00", "currency": "TRY" },
      "trip_cash_in": { "amount": "0.00", "currency": "TRY" },
      "net_cash_flow": { "amount": "12000.00", "currency": "TRY" }
    }
  ],
  "trip_position_memo": [
    {
      "trip_id": "…",
      "trip_name": "Rome",
      "net": { "amount": "200.00", "currency": "EUR" },
      "unrecorded": { "amount": "0.00", "currency": "EUR" }
    }
  ]
}
```

`net_cash_flow` means **actual cash flow**, and `personal_spending` means **non-trip spending plus allocated trip shares**. They differ on purpose (`FINANCIAL_RULES.md` §16.2a). `trip_position_memo` is never part of any balance.

---

## 8. Exchange Rates

**Built in M5** (for the signed-in user; rate limited to 60 a minute per client because they can reach a provider):

| Method & path | Notes |
|---|---|
| `GET /fx/convert?from=&to=&amount=&date=` | The stored reference for a day (fetched if needed) and the converted amount. Response `{rate, original, converted}`; `rate` has `id`, `rate`, `rate_date` (as the source returned it), `requested_date`, `provider`, `upstream`, `retrieved_at`, `age_days`, `is_stale`, `fallback`. With `rate=` instead, previews a typed rate with no provider call (`rate: null`). Codes: 503 `fx_unavailable`, 422 `fx_pair_unsupported`, `same_currency`, `date_in_future`, `invalid_rate` |
| `GET /equivalents?amount=&currency=&targets=` | Display only. Items with `value`, `unit` (`currency`, `gram`, `troy_ounce`, `btc`), `unit_price`, `kind` (`daily_reference` or `spot`), `provider`, `rate_date` or `as_of`, `is_stale`, `status` (`ok` or `unavailable`). Targets default to the person's favourites |
| `GET /equivalents/targets` · `PUT` | Favourites: currency codes, `XAU_G`, `XAU_OZ`, `BTC`; at most 12 |

`POST /transactions` and `PATCH /transactions/{id}` accept an amount in another currency with `conversion`: `{method: "reference", rate_id, accept_stale?}`, `{method: "manual_rate", rate}`, or `{method: "actual_amount", account_amount, rate_id?}`. Codes: `conversion_required`, `conversion_not_needed`, `fx_rate_mismatch`, 409 `fx_rate_stale`, `conversion_rounds_to_zero`. The response adds `original_amount`, `fx` (`method`, `rate`, `rate_date`, `source`, `provider`, `upstream`) and `warnings` (`rate_far_from_reference`).

**Planned contract (from the original specification):**

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /fx/currencies` | User | Supported currencies and exponents |
| `GET /fx/rates?from=USD&to=TRY&date=2026-11-05` | User | Resolve a rate; cached in `fx_rates` |
| `POST /fx/rates/manual` | Member | Create manual rate for a trip |

**`GET /fx/rates`**

```json
{
  "id": "…",
  "from": "USD",
  "to": "TRY",
  "rate": "34.251700000000",
  "rate_date": "2026-10-30",
  "requested_date": "2026-11-01",
  "source": "provider",
  "provider": "frankfurter",
  "upstream": "ECB",
  "retrieved_at": "2026-11-05T09:00:00Z",
  "is_stale": false,
  "age_days": 2
}
```

503 `fx_unavailable`, when no cached rate exists; the client should offer manual entry.

---

## 9. Trips, Membership, Invitations

| Method & path | Auth | Notes |
|---|---|---|
| `GET /trips` | User | Own trips. Filter: `status` |
| `POST /trips` | User [I] | Creator becomes owner |
| `GET /trips/{id}` | Member+ | Includes caller's role |
| `PATCH /trips/{id}` | Admin | If-Match. Base currency: 409 once expenses exist |
| `POST /trips/{id}/complete` | Admin | 409 `pending_items_exist` while expense versions are pending |
| `POST /trips/{id}/reopen` | Admin | Completed → active |
| `POST /trips/{id}/archive` | Owner | Completed → archived; 409 `balances_outstanding` unless the body has `acknowledge_outstanding: true` |
| `POST /trips/{id}/unarchive` | Owner | Archived → completed |
| `GET /trips/{id}/members` | Member+ | Display name, role, status. **No emails** |
| `PATCH /trips/{id}/members/{user_id}` | Owner | Change role |
| `POST /trips/{id}/transfer-ownership` | Owner | Atomic; new owner must be active |
| `POST /trips/{id}/leave` | Member | 409 `balance_outstanding` or `pending_items_exist` |
| `DELETE /trips/{id}/members/{user_id}` | Admin | Becomes `removed`; history kept. Can't remove the owner |
| `GET /trips/{id}/invitations` | Admin | |
| `POST /trips/{id}/invitations` | Admin | Returns link **once** |
| `DELETE /trips/{id}/invitations/{inv_id}` | Admin | Revoke |
| `POST /invitations/preview` | User | Token in body; shows trip name, inviter, dates |
| `POST /invitations/accept` | User | Token in body |
| `POST /invitations/decline` | User | Token in body |

The token travels in the request **body**, not the URL, so it doesn't land in logs. The link format is `https://<host>/invite#<token>`.

---

## 10. Trip Expenses

| Method & path | Auth | Notes |
|---|---|---|
| `GET /trips/{id}/expenses` | Member+ | Filters: `status`, `payer`, `beneficiary`, `category_id`, `currency`, `from`, `to`, `q` |
| `POST /trips/{id}/expenses/preview` | Member | Computes shares; writes nothing |
| `POST /trips/{id}/expenses` | Member [I] | Optional `link` if caller is payer |
| `GET /trips/{id}/expenses/{eid}` | Member+ | Current, pending, and full history |
| `POST /trips/{id}/expenses/{eid}/versions` | Proposer, payer, Admin [I] | Correction |
| `POST /trips/{id}/expenses/{eid}/cancel` | Proposer, payer, Admin [I] | Cancellation version |
| `POST /trips/{id}/expense-versions/{vid}/approve` | Party: payer [I] | Optional `link` |
| `POST /trips/{id}/expense-versions/{vid}/reject` | Party: payer [I] | Optional `reason` |
| `POST /trips/{id}/expense-versions/{vid}/withdraw` | Proposer, Admin | Pending only |
| `GET /trips/{id}/disputes` | Member+ | Filter: `status`. Former members see only those in their scope |
| `POST /trips/{id}/expenses/{eid}/disputes` | Beneficiary [I] | Body: `version_id`, `reason`. 409 `dispute_already_open`, `version_not_current` |
| `POST /trips/{id}/disputes/{did}/withdraw` | Raiser | |
| `POST /trips/{id}/disputes/{did}/resolve` | Admin, not an interested party | Body: `outcome` (`upheld`, `dismissed`), `note`. 403 `interested_party` |

**`POST /trips/{id}/expenses`**

```json
{
  "payer_user_id": "…",
  "description": "Dinner",
  "category_id": "…",
  "expense_date": "2026-11-05",
  "amount": { "amount": "100.00", "currency": "USD" },
  "rate": { "rate_id": "…" },
  "split": {
    "method": "percentage",
    "shares": [
      { "user_id": "…", "value": "50" },
      { "user_id": "…", "value": "50" }
    ]
  },
  "notes": null,
  "acknowledge_stale_rate": false,
  "replaces_expense_id": null,
  "link": null
}
```

`rate` is one of:
- `null`, when the currency equals base.
- `{ "rate_id": "…" }`, from a lookup or a prior manual entry.
- `{ "manual": { "rate": "34.25", "note": "Airport bureau" } }`.

`split.shares[].value` is omitted for `equal`.

**Expense response**

```json
{
  "id": "…",
  "status": "approved",
  "is_corrected": true,
  "has_pending_correction": false,
  "classification": "shared",
  "payer": { "user_id": "…", "display_name": "Alice" },
  "created_by": { "user_id": "…", "display_name": "Bob" },
  "current_version": {
    "id": "…",
    "version_no": 2,
    "description": "Dinner",
    "expense_date": "2026-11-05",
    "amount": { "amount": "100.00", "currency": "USD" },
    "base_amount": { "amount": "3425.17", "currency": "TRY" },
    "rate": {
      "rate": "34.251700000000",
      "source": "provider",
      "upstream": "ECB",
      "rate_date": "2026-10-30",
      "is_manual": false
    },
    "split_method": "equal",
    "shares": [
      {
        "user_id": "…",
        "share": { "amount": "33.34", "currency": "USD" },
        "base_share": { "amount": "1141.95", "currency": "TRY" }
      }
    ]
  },
  "pending_version": null,
  "disputes": { "open_count": 1, "raised_by_me": false }
}
```

**Former members:** the same endpoints return a narrower result set (`SECURITY.md` §4.4). Expenses outside their scope return 404.

**Key errors:** `split_sum_mismatch`, `percentage_sum_invalid`, `payer_not_active_member`, `beneficiary_not_active_member`, `rate_required`, `stale_rate_not_acknowledged`, `conversion_rounds_to_zero`, `correction_already_pending`, `trip_not_active`, `not_payer`, `version_not_pending`.

---

## 11. Settlements and Balances

| Method & path | Auth | Notes |
|---|---|---|
| `GET /trips/{id}/balances` | Member+ | Each member's paid, owed, sent, received, net |
| `GET /trips/{id}/settlements/suggestions` | Member+ | Computed; never stored |
| `GET /trips/{id}/settlements` | Member+ | Filters: `state`, `kind`, `member`, `from`, `to` |
| `POST /trips/{id}/settlements` | Member+ [I] | Caller must be the payer |
| `GET /trips/{id}/settlements/{sid}` | Member+ | |
| `POST /trips/{id}/settlements/{sid}/confirm` | Party [I] | Payment: the recipient, with optional `link`. Reversal: the non-initiator, with no `link` |
| `POST /trips/{id}/settlements/{sid}/reject` | Party [I] | Same party |
| `POST /trips/{id}/settlements/{sid}/cancel` | Initiator | Pending only |
| `POST /trips/{id}/settlements/{sid}/reverse` | Either party [I] | Creates pending reversal |

Former members use `Member+` here so they can still settle outstanding debts. Settlements are allowed on `active` and `completed` trips, not on `archived`.

**`POST /trips/{id}/settlements`**

```json
{
  "recipient_user_id": "…",
  "amount": { "amount": "40.00", "currency": "EUR" },
  "paid_on": "2026-11-05",
  "rate": null,
  "settle_in_full": false,
  "corrects_settlement_id": null,
  "note": "Bank transfer",
  "link": null
}
```

Response includes `warnings: ["exceeds_outstanding_balance"]` if applicable. That's a warning; the record is created.

**`GET /balances`**

```json
{
  "base_currency": "EUR",
  "as_of": "2026-11-05T14:30:00Z",
  "members": [
    {
      "user_id": "…",
      "display_name": "Alice",
      "membership_status": "active",
      "paid": { "amount": "300.00", "currency": "EUR" },
      "owed": { "amount": "230.00", "currency": "EUR" },
      "sent": { "amount": "0.00", "currency": "EUR" },
      "received": { "amount": "0.00", "currency": "EUR" },
      "net": { "amount": "70.00", "currency": "EUR" }
    }
  ],
  "pending_summary": { "expenses": 2, "settlements": 1 }
}
```

**`GET /suggestions`**

```json
{
  "items": [
    {
      "from": { "user_id": "…", "display_name": "Bob" },
      "to": { "user_id": "…", "display_name": "Alice" },
      "amount": { "amount": "40.00", "currency": "EUR" }
    }
  ],
  "based_on": "confirmed_only",
  "note_code": "suggestions_not_guaranteed_minimal",
  "involves_disputed_expense": false,
  "pending_settlements_between_listed_members": []
}
```

---

## 12. Personal Links

**Owner only.** Nothing here is reachable by other trip members, and no trip endpoint reveals link data.

| Method & path | Auth | Notes |
|---|---|---|
| `GET /links` | User | Filter: `status`, `trip_id` |
| `GET /links/suggestions?expense_id=` or `settlement_id=` | User | Candidates for attaching |
| `GET /me/possible-duplicates` | User | Computed list, with match strength (`FINANCIAL_RULES.md` §11.7) |
| `POST /me/possible-duplicates/dismiss` | User | Body: `transaction_id`, `expense_id`. Stored per pair |
| `POST /links` | User [I] | Create new or attach existing |
| `GET /links/{id}` | User | |
| `POST /links/{id}/resolve` | User [I] | For `needs_review`: `update`, `keep`, `reverse` |
| `DELETE /links/{id}?personal_transaction=convert\|reverse` | User [I] | Unlink. `convert` makes it a plain `expense` or `income` again |

**`POST /links`**

```json
{
  "expense_id": "…",
  "mode": "create",
  "account_id": "…",
  "posted_amount": { "amount": "3425.17", "currency": "TRY" },
  "category_id": "…",
  "effective_date": "2026-11-05"
}
```

`category_id` isn't accepted: `trip_*` transactions have no category (`FINANCIAL_RULES.md` §11.3).

Or attach: `{ "expense_id": "…", "mode": "attach", "transaction_id": "…" }`. **Attaching voids that transaction and creates a `trip_*` replacement in the same transaction** (§11.7 of the financial rules). The response shows both.

The same object, minus the IDs, is what the optional `link` field on expense and settlement requests takes.

**Response** includes `linked_base_amount` and the trip's updated `linked` and `unrecorded`. Reversals can't be linked (422 `not_link_eligible`).

**Errors:** `not_link_eligible`, `not_payer`, `not_settlement_party`, `account_archived`, `already_linked`, `transaction_already_linked`, `currency_account_mismatch`.

---

## 13. Reports

All reports share one service and return the same view model consumed by PDF and print.

**As built in M6:**

- `GET /reports?from&to&all_history&type&account_id&category_id&currency&q&grain` → `{generated_at, time_zone, filters, balances_applicable, by_currency[{currency, totals, previous, balances|null, categories[], grain, series[]}], transaction_count, recurring[], debts[]}`. `all_history` starts at the first transaction; other ranges are capped at ten years.
- `GET /reports/export.csv` (same filters, `locale`): UTF-8 with BOM, CRLF, fixed English column keys (`date, type, description, category, account, counter_account, amount, currency, original_amount, original_currency, fx_rate, fx_method, notes, transaction_id, recorded_at`), localized values, signed amounts (money out negative), formula-injection safe. `413 export_too_large` above `csv_max_rows` (50,000).
- `GET /reports/export.pdf` (same filters, `locale` en/ar/tr): A4, synchronous, the transaction table capped at `pdf_max_rows` (2,000) with a note when cut. `503 pdf_unavailable` if the renderer can't start.
- Both exports: 10 a minute per client (`429`).


### 13.1 Personal

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /reports/personal/summary` | User | Per-currency summary and identity |
| `GET /reports/personal/transactions` | User | Detail table, paginated |
| `GET /reports/personal/print` | User | Full data for print view, up to its limit |
| `GET /reports/personal/transactions.csv` | User | Stream: cash movements |
| `GET /reports/personal/trip-allocations.csv` | User | Stream: own allocated shares, and trip positions |
| `GET /reports/personal/changes?from=&to=&since=` | User | "What changed since this report?" (`FINANCIAL_RULES.md` §16.9) |

**Filters:** `period=custom|month|year`, `from`, `to`, `month`, `year`, `account_id` (repeatable), `category_id` (repeatable), `type` (repeatable), `currency` (repeatable), plus `q` and `include_reversed` for the table.

**Summary**

```json
{
  "generated_at": "2026-11-05T14:30:00Z",
  "time_zone": "Europe/Istanbul",
  "filters": { "from": "2026-10-01", "to": "2026-10-31" },
  "balances_applicable": true,
  "by_currency": [
    {
      "currency": "TRY",
      "opening_balance": { "amount": "10000.00", "currency": "TRY" },
      "initial_balances": { "amount": "0.00", "currency": "TRY" },
      "income": { "amount": "30000.00", "currency": "TRY" },
      "net_expenses": { "amount": "18000.00", "currency": "TRY" },
      "trip_cash_out": { "amount": "0.00", "currency": "TRY" },
      "trip_cash_in": { "amount": "0.00", "currency": "TRY" },
      "transfers_in": { "amount": "0.00", "currency": "TRY" },
      "transfers_out": { "amount": "0.00", "currency": "TRY" },
      "conversions_in": { "amount": "0.00", "currency": "TRY" },
      "conversions_out": { "amount": "3500.00", "currency": "TRY" },
      "adjustments": { "amount": "0.00", "currency": "TRY" },
      "closing_balance": { "amount": "18500.00", "currency": "TRY" },
      "net_cash_flow": { "amount": "12000.00", "currency": "TRY" },
      "trip_allocation": { "amount": "0.00", "currency": "TRY" },
      "personal_spending": { "amount": "18000.00", "currency": "TRY" },
      "transaction_count": 58,
      "by_category": [],
      "by_account": []
    }
  ],
  "trip_positions": [],
  "allocation_applicable": true
}
```

With a category or type filter active, `balances_applicable` is `false`, and the balance and identity fields are `null`. With an account filter active, `allocation_applicable` is `false`, and the allocation fields are `null` (`FINANCIAL_RULES.md` §16.3).

**`trip_positions`:** one row per trip the user has a share or link in: base currency, `allocated`, `net`, `linked`, `unrecorded`. Owner-only.

**`trip_allocation`** (`include`, default; `exclude`; `only`) controls spending figures only. It changes nothing else in the response, and `personal_spending` reports which mode was used.

**Reports include** `effective_date`, `recorded_at`, and `corrected_at` per row, plus `possible_duplicates` as a count.

### 13.2 Trip

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /trips/{id}/reports/summary` | Member+ | Totals, categories, balances, suggestions, settlements, pending |
| `GET /trips/{id}/reports/expenses` | Member+ | Detail, paginated |
| `GET /trips/{id}/reports/participants/{user_id}` | Member+ | Participant report |
| `GET /trips/{id}/reports/print` | Member+ | Print data |
| `GET /trips/{id}/reports/changes?since=` | Member+ | Version changes since a report's `generated_at`. Former members see only their scope |
| `GET /trips/{id}/reports/expenses.csv` | Member+ | Expense details |
| `GET /trips/{id}/reports/balances.csv` | Member+ | Participant balances |
| `GET /trips/{id}/reports/settlements.csv` | Member+ | Settlement records |

**Former members** see only trip data that concerns them, per `SECURITY.md` §4.4.

**Pending and rejected items** are returned in clearly separate fields and are never summed into totals. **Disputed items** carry a marker, and their amounts stay in totals until a correction changes them.

**Participant reports** accept `include_private=true` only when the caller is that participant. Anyone else gets 403.

### 13.3 Exports

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /exports` | User [I] | Start PDF export. Returns 202 |
| `GET /exports` | User | Own recent exports |
| `GET /exports/{id}` | User (creator) | Status |
| `GET /exports/{id}/download` | User (creator) | Stream file |
| `DELETE /exports/{id}` | User (creator) | Delete early |

**`POST /exports`**

```json
{
  "report_type": "trip",
  "format": "pdf",
  "trip_id": "…",
  "participant_user_id": null,
  "orientation": "portrait",
  "locale": "ar",
  "include_private": false,
  "filters": {}
}
```

`locale` defaults to the user's language. Figures never vary with it (INV-24).

**Response (202)**

```json
{
  "id": "…",
  "status": "queued",
  "poll_url": "/api/v1/exports/…",
  "expires_at": null
}
```

**Errors:** 429 `export_rate_limited`, 409 `export_in_progress`, 413 `export_too_large` with `row_count` and `limit`.

### 13.4 CSV format

- UTF-8 with BOM, comma delimiter, CRLF line endings, RFC 4180 quoting.
- Amounts: `.` decimal separator, no thousands separators, in separate `amount` and `currency` columns.
- Dates: ISO 8601. Gregorian calendar and Western digits in every language.
- **`profile=standard`** (default): the conventions above. Headers and status labels use the export's `locale`.
- **`profile=machine`**: stable English column keys and raw codes, for scripts.
- **`profile=excel`** [Rec]: semicolon delimiter and comma decimal for Turkish spreadsheet locales, which otherwise may read `1234.56` as text (R-26). Arabic with `.` decimals uses `standard`.
- CSV never contains bidirectional formatting marks in numeric or code columns, and does not reorder columns for RTL.
- Text fields beginning with `=`, `+`, `-`, `@`, tab, or CR are prefixed with `'`, including when those characters follow invisible or bidirectional marks. Numeric columns are server-generated, so they are never prefixed. Details in `SECURITY.md` §7.
- `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`.
- If a stream fails mid-way, the connection is aborted rather than ended normally, and the file ends with no fake completion marker. Clients treat an aborted download as failure.
- A `#`-prefixed metadata row isn't included, so that the files stay directly parsable.

---

## 14. Operations

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /healthz` | Public | Process alive |
| `GET /readyz` | Public | Database reachable, migrations current; reveals no details |
| `GET /openapi.json` | Demo: public. Production: decided at step PD3 | |

---

## 14a. Demo-Only Endpoints

**Mounted only when the application runs as the demo environment.** A test asserts that these routes don't exist in production builds.

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /demo/sandboxes` | Public, tightly rate-limited | Creates an isolated synthetic sandbox, with its persona users, and logs in as the first one |
| `GET /demo/sandboxes/current` | User | Personas, and next reset time |
| `POST /demo/sandboxes/current/switch` | User | Body: `persona_id`. Issues a real session for another persona **in the same sandbox only** |

These use normal sessions, and every other endpoint enforces normal authorization. **Google sign-in and email aren't available in the demo, and these demo routes don't exist in production builds.** Rules are in `SECURITY.md` §14.

## 14b. Availability by Phase

This whole specification remains the approved contract. Endpoints are built in the phases below.

| Group | Phase |
|---|---|
| Google sign-in, logout, CSRF, `/me`, sessions | A (A2) |
| Accounts, transactions, transfers, system categories, dashboard | A |
| Trips (basic), members, expenses, approvals, FX, balances, settlements, suggestions, links | B |
| Real email provider, custom categories, budgets, analytics, invitations, full membership, disputes, reports, exports | C |
| Bot check, quotas at launch levels, deletion, privacy requests | D |

---

## 15. Rate Limits (initial)

| Endpoint group | Limit |
|---|---|
| Google sign-in start | 20 per 10 min per client address **[built, per process]** |
| Google sign-in callback | 30 per 10 min per client address **[built, per process]** |
| Profile changes, logout, session revocation | 60 per minute per client address **[built, per process]** |
| New accounts | A global daily cap, set before public production (`SECURITY.md` §13.1) |
| Invitation preview, accept | 20 per hour per user |
| FX manual and lookup | 60 per minute per user |
| Exports | §13.3 and `ARCHITECTURE.md` §6.4 |
| Disputes | 20 per day per user |
| Privacy requests | 5 per day per IP |
| General authenticated | 300 per minute per user |

Rate limits, quotas, and global safeguards are defined in `SECURITY.md` §13.

## M6.1 changes (2026-11-07)

- `GET /summary/equivalent?target=USD&month=YYYY-MM&refresh=false` → `{month, target, parts[{currency, spent, status: same|ok|unavailable, equivalent|null, rate|null (1 target = rate currency), reason, provider, upstream, rate_date, retrieved_at, is_stale}], combined|null, complete, is_stale}`. Display only; rate limited with the other FX reads (60/min). 422 for an unknown target.
- `GET /reports` adds `table_count` (rows the transaction table holds, after the description search) and `limits {pdf_rows, csv_rows}`.
- `GET /reports/export.pdf` adds `X-Report-Complete`, `X-Report-Rows-Included`, `X-Report-Rows-Total` headers, and the PDF's first page states "Complete report: all N…" or "PARTIAL REPORT: lists N of M…". New error `503 pdf_busy` when the per-process browser limit is reached.
- `GET /budgets/detail` adds `excluded[]`: the same category's expenses in the month posted in other currencies (listed, not counted).
- **Print** in the web app prints the same PDF (complete filtered report from the API), never the rows loaded on screen.

## Trip Mode T1 (2026-11-09)

Built as in §9 and §10 except: no disputes yet; `category_key` replaces `category_id`; `rate` is `{rate_id}` or `{manual: "34.25"}`. Extra: `GET /trips/{id}/activity` (active members only) and `GET /trips/{id}/dashboard` (`total`, `shared`, `personal`, `categories`, per-member `paid/owed/net`, `suggestions`, `pending_count`, `waiting_on_me`, `balanced`). A non-member gets 404 everywhere; a former member gets a scoped list, their own balance and suggestions, and 403 on the activity feed. Invitations: 7 days, single use; preview/accept/decline take the token in the body; 404 `invitation_invalid`, 410 `invitation_used` / `invitation_expired`, 409 `already_member`.

## Trip Mode B4 (2026-11-10)

Built as §11: `GET /trips/{id}/balances`, `GET /trips/{id}/settlements/suggestions`, `GET/POST /trips/{id}/settlements`, `GET /trips/{id}/settlements/{sid}`, `POST …/confirm|reject|cancel|reverse` (confirm/reject/reverse need Idempotency-Key). Settlement responses carry `state` and a display `status` (`reversal_pending`, `reversed`), `can` flags, and `waiting_on_me`. Errors: `not_counterparty`, `settlement_not_pending`, `reversal_exists`, `possible_duplicate`, `currency_not_supported`, `recipient_not_member`. Disputes as §10: `GET /trips/{id}/disputes`, `POST /trips/{id}/expenses/{eid}/disputes`, `POST /trips/{id}/disputes/{did}/withdraw|resolve` (403 `interested_party`, 409 `dispute_already_open`, `version_not_current`). Expenses gain `disputes {open_count, mine}` and, in detail, `dispute_history`; the dashboard gains `sent/received` per member, `settlements_waiting`, `pending_settlements`, `disputes_attention`, and `involves_disputed_expense` on suggestions.

## Trip Mode B5 (2026-11-11)

**Personal (owner-only; never part of a trip response):** `GET /links?trip_id=&include_unlinked=`, `POST /links` (Idempotency-Key; `mode: create` with `account_id`, `amount` in the account's currency, `effective_date`; or `mode: attach` with `transaction_id`, `transaction_version`, `confirm: true`), `GET /links/suggestions?trip_id=` (what the person may still record), `GET /links/{id}`, `POST /links/{id}/resolve` (`update` with `amount` | `keep` | `reverse`; Idempotency-Key), `DELETE /links/{id}?personal_transaction=reverse|convert` (idempotent), `GET /me/possible-duplicates?trip_id=&expense_id=`, `POST /me/possible-duplicates/dismiss` (204), `GET /me/trips/{id}/position` (paid, share, net, owed_to_me, i_owe, settled and pending sent/received, cash_position, linked, unrecorded, needs_review, the person's shares). Errors: `not_payer`, `not_party`, `expense_not_approved`, `settlement_not_confirmed`, `settlement_reversed`, `reversal_not_linkable`, `already_linked`, `not_attachable`, `confirmation_required`, `nothing_to_update`, `link_not_active`; `POST /transactions/{id}/void` on a trip entry is 409 `managed_by_trip_link`.
**Changed:** transactions carry `trip_link` and `replaces_transaction_id`, and accept the three trip types as a filter; `/summary` adds `trip_shares`, `trip_paid`, `trip_received` (null without trip activity); Insights and Reports accept `trip_allocation` and return `totals.non_trip_expenses`, `totals.trip_shares`, per-currency `trips`, and `balances.trips_in/trips_out`; `GET /reports/trip-shares.csv`; budget detail adds `trip_shares`; account activity signs trip entries (paid out −, received +).
**Trip report (any current or former member; former: their part):** `GET /trips/{id}/reports/summary?from=&to=&category=&member_id=`, `…/reports/expenses.csv`, `balances.csv`, `settlements.csv`, `export.pdf?locale=` (with `X-Report-Complete`, `X-Report-Rows-Included`, `X-Report-Rows-Total`). Print uses the PDF.

## Everyday milestone (2026-11-12)

- **Refunds:** `POST /transactions/{id}/refunds` [I] `{amount, account_id?, effective_date?, description?, notes?, conversion?}` → the purchase with `refunds {refunded, refundable, items[]}`. Codes: 409 `not_refundable`, `refund_exceeds_original` (params `refundable`, `currency`), `has_refunds` (void or correct a refunded purchase); 422 `refund_currency_mismatch`, `refund_before_purchase`, `conversion_required`. Transactions carry `refunds_transaction_id`; `type=refund` filters; reports' balances add `refunds`. `POST /transactions` accepts `source: template`.
- **Templates:** `GET /templates?include_archived`, `POST /templates` [I] (manual fields, or `from_transaction_id`), `PATCH /templates/{id}` (If-Match; `clear_amount`, `clear_account`, `status`), `DELETE /templates/{id}` (204), `POST /templates/{id}/used`.
- **Goals:** `GET /goals?include_archived`, `POST /goals` [I], `GET /goals/{id}` (with history), `POST /goals/{id}/add|reduce` [I] `{amount, note?}`, `PATCH /goals/{id}` (If-Match; name, target, target_date, clear_target_date, status). Codes: 409 `goal_not_active`, `goal_progress_negative`.
- **Category groups:** `GET/POST /category-groups`, `PATCH /category-groups/{id}` (If-Match; name, status), `PUT /categories/{id}/group` `{group_id|null}`, `GET /category-groups/totals?from&to&currency&trip_allocation`. Categories carry `group_id`. 409 `group_name_taken`, `group_archived`.
- **Calendar:** `GET /calendar?month&currency&account_id` → days (income, spending, count, trip shares) and the month's occurrences.
- **Subscriptions:** recurring rules accept `is_subscription` (422 `invalid_subscription` unless a monthly or yearly expense); `GET /subscriptions?days` → items, costs per currency, upcoming occurrences.
- **Trip settlements:** `POST /trips/{id}/settlements` accepts any currency with `rate {rate_id|manual}` or `base_amount`; responses add `base_amount` and `rate`; `currency_not_supported` is replaced by `rate_required` / `conversion_not_needed`. Trip report payments add `paid`.
- **One-step linking:** `POST /links/expense` [I] `{trip_id, expense, link {account_id, amount, effective_date?}}`; `POST /links/approve` [I] `{trip_id, version_id, link}`. 403 `not_payer`, 409 `already_linked`.

## Expansion milestone routes (2026-11-14)

All private (`Cache-Control: private, no-store`), session and CSRF as everywhere.

**Installments (§4.13)**
- `GET /installments[?include_archived]`, `GET /installments/{id}` (with schedule, superseded rows, payments; ETag)
- `POST /installments/preview` (saves nothing), `POST /installments` (Idempotency-Key)
- `POST /installments/{id}/payments` (Idempotency-Key): `principal`, `principal_account_amount`, `interest`, `fee`, `paid_on`
- `POST /installments/{id}/reschedule`, `PATCH /installments/{id}` (name, lender, notes, goal), `POST /installments/{id}/archive|restore|remove` (If-Match)
- `GET /installments-overview`: per currency, monthly commitment, remaining principal and scheduled interest, due within 1/3/6/12 months, overdue, average recorded income and the share; upcoming installments
- Errors: `exceeds_remaining`, `interest_account_currency`, `plan_has_payments`, `plan_not_settled`, `goal_follows_plan`, `managed_by_installment_plan`, …

**Totals:** `GET /balances/total?target=XXX[&refresh=true]`: active accounts with equivalents, each currency's exact balance, the combined figure (null if any rate is missing), and the rates used. Display only, nothing is stored. Debts and installment plans are never included. Net worth was removed by owner decision; `/wealth` no longer exists.

**Budgets:** `POST /budgets/restore` (category, currency, from month), `POST /budgets/delete` (409 `budget_has_history`).

**Categories:** `PUT /categories/{id}/favourite` `{favourite}`; categories carry `is_favourite`.

**Trips:** `POST /trips/{id}/cancel|reinstate` (admin); trips carry `phase`, `cancelled_at`, `shared_budget`; `PATCH /trips/{id}` accepts `shared_budget`. `GET|POST /trips/{id}/plan`, `PATCH|DELETE /trips/{id}/plan/{item}` (who added it, or an admin); `GET|PUT /trips/{id}/my-budget` (the caller's own only); `GET /trips/{id}/analytics` (planned vs actual, by kind and category, daily, per member, the caller's share vs cash paid and budget, pending and outstanding). All are in the trip permission matrix test.

**Calendar:** `GET /calendar` also returns `installments` expected that month. **Goals** carry `plan_id`/`plan_name` when following a plan.

## Home dashboard and removal routes (2026-11-15)

- `GET /auth/me/home-layout`, `PUT /auth/me/home-layout` (body `{columns: [[…],[…]], hidden: […]}`, or no body for the default; 422 `unknown_widget`, `duplicate_widget`, `widget_required`).
- `GET /balances/total?target=XXX[&refresh=true]`: accounts, per-currency totals and conversions, combined (null if a rate is missing), rates. Never liabilities.
- `DELETE /categories/{id}` (409 `category_is_default`, `category_has_entries`, `category_used_by_budget|recurring|template`).
- `DELETE /recurring/rules/{id}` (If-Match; 409 `rule_has_payments`).
- `DELETE /accounts/{id}` now also answers 409 `account_used_by_recurring` / `account_used_by_template`, and clears the Quick Add default when it was that account.

### Home's data (2026-11-09)

- `GET /summary` per currency also returns `categories` (every category with spending, largest first; `top_categories` stays the first five) and `uncategorized` (spending with no category, or null).
- `GET /summary/equivalent?target=XXX` returns the month's spending as before, and its income in `received` (same shape).
- `GET /installments-overview`: each `upcoming` item carries `debt_id`, so Home can show an installment once when a recurring rule follows the same debt.
- `GET` and `PUT /auth/me/home-layout`: `{"rows": [[...]], "hidden": [...]}`, one to three sections per row. Sections: `summary, recent, categories, upcoming, budgets, goals, templates, links`. A layout stored as two columns is read as rows and rewritten on the next save.

### Spending Intelligence

`GET /analytics/intelligence?period=mtd|qtd|half|ytd|rolling6|last12|custom[&as_of][&from&to][&currency][&account_id][&category_id][&type=income|expense][&trip_allocation][&trend_end=YYYY-MM][&equivalent=XXX]`. Display only. Definitions: FINANCIAL_RULES §16.6.

### Account changes while deletion is pending

While the account is `deletion_pending`, every change through `require_writable_user` answers **409 `deletion_pending`**. Reading, downloading, signing out and cancelling still work.
