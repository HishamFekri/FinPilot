# FinPilot — Security and Privacy

**Status:** Planning document, updated as the project develops · **Date:** 2026-11-05 · **Incorporates:** OD-1 to OD-5, OD-17, OD-21, OD-24, and the language decision. **Neither the portfolio demo nor production has been cleared:** see §13 and §14.
**Role:** Security and privacy design. Informed by OWASP guidance (Session Management, CSRF Prevention, Authorization, CSV Injection, and the API Security Top 10), and by Google's OpenID Connect documentation (read 2026-11-05).

**FinPilot claims no regulatory or financial compliance.** "Informed by OWASP" means the guidance shaped the design, not that anything has been audited or certified. Only the OWASP password parameters, the WeasyPrint guidance, and GitHub's runner guidance below were read directly from source during this planning phase. The other OWASP references rely on prior knowledge, and should be checked against the current cheat sheets during step A2.

---

## 1. Threat Model Summary

| Asset | Threat | Primary controls |
|---|---|---|
| Personal financial data | Other users reading it (IDOR) | Scoped queries, composite FKs, automated authorization tests |
| Personal data, trip context | Trip admin overreach | Roles grant no access to private data (D8) |
| Trip obligations | Forged approvals or confirmations | Party-only authorization; actions tied to a specific version |
| Sessions | Theft through XSS or logs | `HttpOnly` cookie, CSP, hashed storage, no token logging |
| Accounts | Login CSRF, replayed callbacks, forged ID tokens | State bound to the browser, single use, nonce, PKCE, and full ID token verification (§2.7) |
| Accounts | Takeover through a reused or reassigned email address | Google's subject ID is the identity. An email address never identifies or links anyone (§2.5) |
| Invitations | Link leak or guessing | 256-bit single-use token, hashed, expiring, revocable |
| Exports | Download by the wrong user | Per-request authorization; no public URLs |
| CSV files | Formula injection | Cell escaping |
| PDF renderer | Malicious content, SSRF | Autoescape, blocked fetches, resource limits |
| Deployment host | A compromise spreading beyond the app | Runner isolation, non-root containers, no Docker socket, network separation |
| Service availability | Bots, mass signups, report floods from the open internet | Bot checks, rate limits, quotas, bounded queues, global caps (§13) |
| Displayed amounts and names | Spoofing with invisible bidirectional characters | Input sanitizing, isolated rendering (§6) |
| Personal data | Inability to honor a legal erasure request | Separate tracked process (§11) |
| Backups | Loss or theft | Encryption, off-site copy, restore tests |

---

## 2. Authentication

### 2.1 Sign-in method: Google only [Approved, supersedes OD-12's earlier scope]

**Google OAuth is FinPilot's only way to authenticate,** for the web MVP, public production, and the future Android and iOS apps.

**There's no password, password reset, registration form, or FinPilot verification email.** The first successful Google sign-in creates the account. Google's `sub` claim identifies the person, and FinPilot issues its own session.

**Why this is safer here:** FinPilot never stores or checks a password. That removes credential stuffing, password reset, and email-based recovery, which are the riskiest parts of a password system. **The trade-off is dependence on Google,** covered in §2.4.

### 2.2 Sessions

Opaque random 256-bit tokens, stored server-side as SHA-256 hashes. A database leak doesn't expose usable tokens. **Status: built in A2.3** (`ARCHITECTURE.md` §5.3).

**Why not JWT?** "Review and revoke active sessions" is a stated requirement, and JWTs need a server-side revocation store to do that, which removes their main benefit. Opaque tokens are simpler and revocable by definition. **Google's ID token is never used as a FinPilot session.**

| Setting | Web | Mobile (future) |
|---|---|---|
| Transport | `__Host-fp_session` cookie | `Authorization: Bearer` |
| Storage | Browser cookie jar; not readable by JS. **Never `localStorage`** | `expo-secure-store` |
| Idle timeout | 7 days | 30 days |
| Absolute timeout | 30 days | 90 days |

**Cookie:** `__Host-` prefix, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`.

**Lifecycle:**
- A new session is issued at every sign-in, and any session the browser already presented is revoked. A token planted in the browser beforehand is never adopted, so no fixation is possible.
- Idle and absolute lifetimes are configurable within bounds (`SESSION_IDLE_HOURS`, `SESSION_ABSOLUTE_HOURS`). Idle can't exceed absolute.
- Several sessions can be active at once. Users can list them and revoke any one, or all others.
- Logout revokes the current session on the server, and clears the cookie.
- Suspension revokes every session.
- Expired rows are purged.
- **During a Google outage, existing sessions keep working,** because checking a session never calls Google.

### 2.3 Re-authentication

**FinPilot is a personal expense tracker, not a bank.** It holds no money and connects to no bank, so ordinary actions, including profile changes, recording entries, and exporting your own data, never ask for a new Google sign-in. A valid session plus CSRF protection is the control.

**Tested against real Google in A2.4:** Google's ID token has **no `auth_time` claim**, and `max_age=0` is **accepted but not enforced**. Google returned an account chooser, didn't ask for the password again, and still left `auth_time` out. **So FinPilot can't prove that someone re-entered their Google password,** and won't pretend to.

**Proposed, for the owner to confirm before C1:** only irreversible actions (deletion request, personal-data erasure) need a **recent sign-in**: the current FinPilot session must have been issued by a Google sign-in, matching the same `sub`, within the last 15 minutes. Otherwise the app sends the person through Google again, which is usually one click. That step plus a typed confirmation and the grace period is the protection. **Export doesn't need it,** because a stolen session could already read the same data through the API.

### 2.4 Account continuity and recovery

**FinPilot has no password to reset, so it never "recovers" an account from an email address alone.** Knowing, or even receiving mail at, an address doesn't prove someone is the Google account behind a FinPilot user.

| Situation | Handling |
|---|---|
| **Email changes at Google** | The `sub` is unchanged, so it's the same FinPilot user. A verified new address replaces the stored copy at the next sign-in, with an audit event |
| **Address reassigned to someone else** (such as a work address) | A new `sub` is a new person, and gets a new account. **It's never given the old account** |
| **User loses access to their Google account** | First, Google's own account recovery. Existing FinPilot sessions keep working until they expire, so data export stays possible on a device that's still signed in. **FinPilot has no self-service way to move an account to a different Google identity** |
| **Permanent loss** | **[Open]** A manual, operator-reviewed process, designed before public production. It must need strong evidence, never just an email address, and every step is audited. Until it exists, the honest answer is that the data can't be reattached |
| **Google outage** | Existing sessions work. New sign-ins get a clear 503 `google_unavailable`. **There's no fallback sign-in method,** because a weaker one would become the attack path |
| **Account suspended** | Sign-in is refused with `account_unavailable`, and every session is revoked |
| **Google account deleted or disabled** | No new sign-ins. Existing sessions end at their timeout. Google's Cross-Account Protection signals could shorten this later **[Proposed]** |

**Users should be told plainly,** in the privacy notice and the settings screen, that their FinPilot account depends on their Google account, and that exports are how to keep a copy.

### 2.5 Email addresses

- **The email is a copy of what Google reports.** It's for display and contact, and **never identifies anyone**.
- **Only a verified email is accepted.** A first sign-in without a verified email creates no account. A later unverified email never replaces the stored one.
- **Emails aren't unique** (`DATABASE.md` §3.2). Google says addresses can change and be reassigned, while `sub` is never reused.
- **Provider aliases aren't collapsed,** and there's no case folding beyond what Google reports.

### 2.6 Enumeration

There's no registration or password form, so the usual probes don't exist. **Sign-in errors are generic:** which check failed is logged and audited, never returned.

### 2.7 Google OAuth [Approved; built in A2.2 and A2.3, tested against a fake Google only]

**Protocol requirements (web):**

| Control | Requirement | Status |
|---|---|---|
| Flow | Authorization code, handled by the backend, with PKCE `S256` | Built |
| Endpoints | From Google's discovery document, cached for an hour. A document pointing outside Google's hosts is refused | Built |
| State | 256-bit random, single use, 10-minute expiry, stored only as a SHA-256 hash, consumed atomically | Built |
| Login CSRF | The state is bound to the starting browser by an `HttpOnly`, `SameSite=Lax` cookie, compared in constant time | Built |
| Replay | The state is consumed and committed **before** Google is called, so a failure afterwards can't reopen it. Google's codes are single use too | Built |
| Nonce | Sent in the request, and checked in the ID token in constant time | Built |
| ID token | PyJWT verifies the RS256 signature against Google's published keys, then issuer, audience, expiry, and issued-at. Other algorithms are refused. **No cryptography is hand-written** | Built |
| Audience | Must equal FinPilot's client ID. A token for several audiences, or with another `azp`, is refused | Built |
| Redirect URI | One exact URI per environment, validated at startup: https outside local development, fixed path, no query, and the same host as the web app | Built |
| After sign-in | Only to a local path on the web app (`return_to`), checked at start and by a database constraint. **Never to a URL from user input** | Built |
| Tokens | Google's access and refresh tokens aren't requested for offline use, stored, logged, or put in URLs. The ID token is discarded after verification | Built |
| Scopes | `openid email profile` only. **No Gmail, Drive, Contacts, or other API access** | Built |
| Responses | `Cache-Control: no-store` and `Referrer-Policy: no-referrer` on sign-in and every account route (`/auth/me`, sessions, logout); account routes were missing it until A2.4. Google's `error_description` is never shown | Built |
| Logs | Query strings, including the code and state, never reach logs | Built |

**Account rules:**

| Situation | Rule |
|---|---|
| Identity | Google's `sub` claim. **An email address never identifies anyone on its own** |
| `sub` already known | Sign in that user, if their status allows it |
| New `sub`, verified email | Create the user and identity in one transaction. Language comes from the page the person started on |
| New `sub`, no verified email | Refuse: `google_email_not_verified`. Nothing is created |
| New `sub`, email held by another account | **Never linked or signed in to.** It's a separate account, because the address may have been reassigned |
| Two callbacks for one new person at once | One user and one identity, enforced by `UQ (provider, provider_subject)`. The loser rolls back completely, and reuses the winner's result |
| Display name | Taken from Google once, with control and bidirectional characters removed. Afterwards it's the user's to edit, and isn't overwritten |

**Credentials:** the client secret is server-side only, separate per environment, and never in the repository, a frontend bundle, or a mobile app. **Development (A2.4):** the owner created the Google Cloud project with an External consent screen in Testing mode, the owner as test user, and one Web application client for development. Its JavaScript origin is `http://localhost:3000`, and its redirect URI is exactly `http://localhost:8000/api/v1/auth/google/callback`. Values live only in `backend/.env` as `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REDIRECT_URI`. **Production needs its own client,** created before public production.

**Future React Native:** the system browser or native Google sign-in, with PKCE and no embedded secret. The API verifies the ID token against an allowlist of FinPilot's client IDs, and the nonce, applies the same account rules, and returns the same revocable opaque session as a bearer token, kept in `expo-secure-store`.

**Required tests** are listed in `ROADMAP.md` §3.3. Automated tests replace Google's HTTP endpoints with a local fake that signs tokens with a test key. They need no real credentials and no network.

### 2.8 MFA

Handled by Google, not FinPilot. Users should be encouraged to protect their Google account with 2-Step Verification.

---

## 3. CSRF and CORS [Built in A2.3]

**Two independent protections are kept separate:**
- **Login CSRF** (an attacker making a victim finish the attacker's Google sign-in) is stopped by the OAuth state, bound to the starting browser (§2.7).
- **Request CSRF** (another site making a signed-in user's browser change something) is stopped by the checks below.

**Every cookie-authenticated `POST`, `PUT`, `PATCH`, or `DELETE` must pass both:**

1. **Origin check.** `Origin` must be FinPilot's web origin or the API's own origin. If a browser omits `Origin`, the origin part of `Referer` is used instead. **If neither is present, the request is refused.**
2. **CSRF token.** At sign-in, FinPilot sets a second cookie, `fp_csrf`, which JavaScript can read. Pages send its value back as `X-CSRF-Token`. The server compares its SHA-256, in constant time, with the hash stored on that session. Another site can't read the cookie, so it can't send the header.

**The session cookie is also `SameSite=Lax`,** which already keeps it off most cross-site requests. That's a useful baseline, and not relied on alone.

**Coverage:** logout, profile changes, session revocation, and every future financial write. A test fails if any state-changing route skips the check. Logout does the check itself, because it must also work without a session.

**CORS:** none is enabled. Production serves the web app and API from one origin, which configuration enforces. In development, the web app reaches the API through its own server, so requests stay same-origin there too.

**Limitations:**
- **Script injection (XSS) on FinPilot's own pages defeats any CSRF defense,** because the script can read `fp_csrf`. The defenses against that are output encoding, React's escaping, and a Content Security Policy, **built in A2.5:** scripts and styles by nonce, and no `unsafe-inline` or `unsafe-eval` for scripts in production (`ARCHITECTURE.md` §5.4).
- In development, the `__Host-` prefix isn't used, because it requires https. Any other program on the same machine could set cookies for `localhost`. This doesn't apply to production.

**Bearer-authenticated requests** (future mobile) skip CSRF checks, since browsers don't attach them automatically. A request may authenticate one way only: sending both a cookie and a bearer token will be rejected. **Bearer tokens aren't accepted yet.**

---

## 4. Authorization

### 4.1 Principles

1. **Enforced on the server, per request.** The UI hides buttons only for convenience.
2. **Every personal query is scoped by `user_id`** from the session, never from client input.
3. **Trip access is checked through one policy module** with named functions, such as `can_propose_correction(actor, expense)`, so rules aren't scattered across routers.
4. **404 for resources the caller may not know about.**
5. **Trip roles never grant access to personal finance data.** There's no code path from `trip_members` to `financial_accounts` (enforced by import-linter).

### 4.2 Trip roles

| Role | Definition |
|---|---|
| **Owner** | One per trip. Creator by default. Transferable |
| **Admin** | Appointed by the owner. Manages trip and members; can propose changes to others' expenses |
| **Member** | Creates expenses, proposes own corrections, settles |
| **Former** | Status `left` or `removed`. Read-only, scoped view, and can complete settlements |

**"Normal" permissions for D7:** "Members can normally modify their own submitted expenses." Here, "own" means they proposed the expense or are its payer. "Appropriate authorization" for others' expenses means admin or owner, with the payer's confirmation still required.

### 4.3 Permission matrix

✓ = allowed · ✗ = denied · "Self" = only own record or role in the action

| Action | Owner | Admin | Member | Former |
|---|---|---|---|---|
| View trip, expenses, balances, members | ✓ | ✓ | ✓ | Scoped (§4.4) |
| Edit trip details | ✓ | ✓ | ✗ | ✗ |
| Complete or reopen trip | ✓ | ✓ | ✗ | ✗ |
| Archive or unarchive trip | ✓ | ✗ | ✗ | ✗ |
| Invite, revoke invitations | ✓ | ✓ | ✗ | ✗ |
| Change roles, transfer ownership | ✓ | ✗ | ✗ | ✗ |
| Remove a member | ✓ | Members only | ✗ | ✗ |
| Leave trip | After transfer | ✓ | ✓ | — |
| Create expense, any active member as payer | ✓ | ✓ | ✓ | ✗ |
| Propose correction or cancellation | ✓ | ✓ | If proposer or payer | ✗ |
| **Raise a dispute** [OD-2] | If beneficiary | If beneficiary | If beneficiary | If beneficiary |
| Withdraw a dispute | If raiser | If raiser | If raiser | If raiser |
| Resolve a dispute | Unless payer, proposer, or raiser | Unless payer, proposer, or raiser | ✗ | ✗ |
| Withdraw pending version | ✓ | ✓ | If proposer | ✗ |
| **Approve or reject expense version** | Payer only | Payer only | Payer only | Payer only |
| Record settlement | Self as payer | Self as payer | Self as payer | Self as payer |
| **Confirm or reject settlement** | Party only | Party only | Party only | Party only |
| Initiate reversal | Party only | Party only | Party only | Party only |
| **View another member's private accounts** | ✗ | ✗ | ✗ | ✗ |
| **See whether someone linked an account** | ✗ | ✗ | ✗ | ✗ |
| Create or resolve own links | Self | Self | Self | Self |
| Export trip reports | ✓ | ✓ | ✓ | Own participant report |

**The bolded lines have no admin override, deliberately.** The trip owner can't approve on someone's behalf, can't confirm a payment on someone's behalf, and can't see anyone's accounts.

### 4.4 When a member leaves or is removed [OD-4: proposed policy]

Membership ends; obligations don't. A former member keeps only what they need to understand and settle what concerns them.

| A former member **can** | A former member **cannot** |
|---|---|
| See the trip's name and dates | See expenses they had no part in, **including other members' personal trip expenses**, past or future |
| See expenses where they are payer, proposer, or beneficiary | Create or correct expenses |
| See their own participant report and settlements | See the wider trip report |
| See their own balance, and the suggested payments that involve them | See the activity feed |
| Record, confirm, or reject settlements involving them | Invite anyone, or see the member list beyond people they share records with |
| Confirm or reject items waiting on them, and dispute an expense they're assigned | |

**Why narrower than an active member:** OD-4's full visibility exists because members are actively sharing a record. Someone who's been removed, possibly after a dispute, has no continuing reason to see other members' personal items.

**Previously seen data:** anything already exported or saved can't be recalled. The notice before recording a personal trip expense says so.

**Other members' view of a former member:** their name stays on historic records, marked "Left" or "Removed", and any balance stays visible until settled.

**On rejoining:** full visibility returns, including the period while they were away.

### 4.5 Trip visibility [OD-4 confirmed]

**A trip expense is shared. A personal financial account is not. Those are different things.**

| Every active member **may** view | No member may view, in any role |
|---|---|
| All trip expense records, **including expenses marked personal within that trip** | Other users' private financial accounts |
| Amounts, payer, beneficiaries, shares, notes, category, rate | Private account balances |
| Corrections, disputes, and their reasons | Unrelated personal transactions |
| All settlements in the trip | Private account-linking metadata: whether a link exists, its account, and the `unrecorded` figure |
| Members' display names and roles | Other members' personal spending totals or budgets. Their share of *this trip* is trip data, but their overall spending isn't |
| | Authentication and profile data: email, sessions, time zone, preferred currency, language, `last_active_at` |

**Enforcement:**

- Trip modules can't import personal finance modules (`ARCHITECTURE.md` §3.3).
- Member responses use a dedicated schema with only `user_id`, `display_name`, `role`, and `status`.
- An automated test plants unique marker strings in every member's private data, then asserts that none of them appears in any trip API response, CSV, or PDF.

**Notice before recording [OD-4]:** the add-expense form states, whenever the selected split is payer-only: *"Personal doesn't mean private. Everyone on this trip can see this expense. Your bank accounts and personal transactions stay private. To keep a purchase private, record it in your personal finance instead."* The same message appears in the invitation screen (`PRD.md` §8.7), and in all three languages.

### 4.6 Member email privacy

Members see display names, not email addresses, of other members. A trip shouldn't become a way to harvest contact information.

### 4.7 Required tests

An automated **authorization matrix test** runs every protected endpoint against each relationship: anonymous, unrelated user, member, admin, owner, former member, payer, recipient. A route with no entry in the matrix fails the build, so a new endpoint can't quietly ship unprotected.

---

## 5. Invitations

| Property | Design |
|---|---|
| Token | 256-bit random, shown once, stored as a hash |
| Delivery | Shareable link, since people often coordinate trips in messaging apps |
| Token location | URL fragment (`/invite#token`), posted in a request body. Fragments aren't sent to servers or leaked via `Referer` |
| Use | Single-use, 7-day expiry, revocable |
| Before accepting | Requires a logged-in, verified account. Shows only trip name, inviter, and dates |
| Role granted | Always `member`. Promotion is a separate owner action |
| Oversight | Admins see who joined; owner or admin can remove them |
| Abuse | Rate-limited, with notifications on join |

A forwarded link could be used by someone unintended. Single-use and visible membership limit the impact. Nobody can gain access to private account data through a trip in any case.

---

## 6. Web Security

| Control | Setting |
|---|---|
| HTTPS | Enforced by proxy; HSTS after the domain is confirmed stable |
| CSP | **Built (A2.5).** `default-src 'self'`; scripts and style elements by per-response nonce, with `'strict-dynamic'`; no inline event handlers; `frame-ancestors 'none'`. One documented exception: inline style *attributes* (`style-src-attr`). Development adds `'unsafe-eval'` and inline styles, for its tooling only. Policy and test results: `ARCHITECTURE.md` §5.4 |
| Other headers | **Built (A2.5):** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` on sign-in and app pages and `strict-origin-when-cross-origin` elsewhere, a restrictive `Permissions-Policy`, and `Cache-Control: private, no-store` on sign-in and app pages |
| XSS | React escaping; `dangerouslySetInnerHTML` banned by lint rule; no user-supplied HTML anywhere |
| SQL injection | SQLAlchemy parameter binding; report sort fields from allowlists |
| Mass assignment | Explicit Pydantic input schemas with `extra="forbid"` |
| Uploads | Only if OD-13 keeps cover images: size cap, content sniffing, re-encoding through Pillow, random names, metadata stripped, never served from the API's origin paths as HTML |
| SSRF | The only outbound calls go to fixed configured hosts (Google sign-in, FX, email, bot check). Google endpoints from discovery are limited to Google's hosts. No user-provided URLs. As built in M5: `api.frankfurter.dev` and `api.coinbase.com`, hard-coded, no redirects followed, 64 KB response cap, no keys |
| **Bidirectional spoofing** | Display names and account names reject bidi override and isolate controls (U+202A–202E, U+2066–2069). Other text fields strip them on export. Each user-written string renders in its own isolated run (`dir="auto"`), so that text can't visually reorder a neighboring amount or name. This matters more now that Arabic is a launch language |
| **Translations** | Treated as code: reviewed, with no raw HTML. Placeholders are validated by CI, so a bad translation can't inject markup or drop an amount |

---

## 7. Report and Export Security

### 7.1 Downloads

- `GET /exports/{id}/download` checks the session owner at every download. **There are no public or pre-signed links.**
- Files have random storage keys, and live outside any web root.
- Files expire after 24 hours, and are also deleted on demand.
- Filenames in `Content-Disposition` are sanitized.
- Authorization is re-checked **when the worker starts a job**, not only on request. Someone removed in the interval doesn't get an export.

**Sharing:** a user can save a PDF and share it themselves. FinPilot's own link never grants access.

### 7.2 PDF generation

WeasyPrint's documentation explicitly warns about untrusted HTML or CSS, including local file access through `file://` and unbounded resource use.

| Risk | Control |
|---|---|
| Injection of markup by user text | Jinja2 autoescape on, and template-only CSS |
| Remote or local file fetch | Custom URL fetcher: **denies everything** except bundled fonts and logo |
| User-supplied CSS | Never accepted |
| Runaway render | Child process with time and memory limits; killed on overrun |
| Container privileges | Non-root, read-only root filesystem, only the exports volume writable |
| Huge input | Row caps, with long text truncated in table cells |

### 7.3 CSV injection

**As built in M6:** every text cell passes `exports.safe_cell`: a value starting with `= + - @`, tab or CR (also after leading invisible marks) gets a leading apostrophe. Numbers are written as plain decimals. Exports need a session, are limited to 10 a minute, and are size-capped (CSV 50,000 rows → 413; PDF 2,000 rows, then a truncation note). The PDF renderer runs with JavaScript off and all network blocked, so a description can't load anything.


Applies to all user-controlled text fields: descriptions, notes, category names, account names, member names, trip names.

| Rule | Detail |
|---|---|
| Escape | Text cells starting with `=`, `+`, `-`, `@`, tab, or CR get a leading `'`. **The check first ignores leading whitespace and invisible or bidirectional marks,** which could otherwise hide a trigger character |
| Numeric columns | Are server-formatted amounts. They're never escaped, because a legitimate `-12.50` must remain numeric |
| Encoding | UTF-8 with BOM for correct display in Excel |
| Quoting | RFC 4180 |

**Tradeoff:** the `'` prefix alters the raw value for programmatic consumers, and this is accepted and documented, since the safer default is better here. Test cases include each trigger character, delimiters inside values, newlines, Arabic and Turkish text, and values that begin with a space and then a trigger.

### 7.4 Report authorization

| Report | Who | Excludes |
|---|---|---|
| Personal | The owner only | — |
| Trip | Active members | Personal links; private accounts; `unrecorded`; duplicate data |
| Participant | Active members; former members for themselves | Unrelated personal finance data. The private cash block appears only for the participant themself, and enters a PDF only when they explicitly ask |
| Account data export | The owner, with a valid session (§2.3) | Other members' private data; trip data limited to what that user could already see |

**Test:** a trip report's PDF, CSV, and JSON are scanned for planted private account names from every member, and none may appear.

---

## 8. Logging and Data Minimization

**Never log:** tokens, OAuth codes and state, cookies, invitation links, amounts, transaction descriptions, notes, account names, full emails, Google subject IDs, or request bodies on financial routes.

**Do log:** request ID, route template (`/trips/{id}`, not the real URL), status, duration, and a pseudonymous user reference.

The reverse proxy needs the same treatment: its access logs shouldn't include query strings on sensitive routes, since report filters can reveal activity.

**Audit metadata** does hold before and after financial values, because that's its purpose. It's protected as financial data, and scoped so private events never appear in trip feeds.

**Error tracking,** if added later, must scrub the same fields.

---

## 9. Secrets and Database Access

| Secret | Handling |
|---|---|
| App secrets, DB passwords | Docker secrets or a root-owned `.env` with `0600` permissions, outside the repository |
| CI secrets | GitHub encrypted secrets, scoped to a protected environment |
| Google OAuth client secret | Server-side only, separate per environment, never in the repository or a client |
| Backup encryption key | **Stored off the server too.** Losing it makes backups worthless |

`.env` is gitignored; a secret scanner runs in CI. Secrets are rotated on suspected exposure, with a short written procedure.

**Database roles (least privilege):**

| Role | Rights |
|---|---|
| `finpilot_migrator` | The schema owner; used only for migrations and maintenance |
| `finpilot_app` | Only the table rights `app/db/hardening.py` lists. **No DDL**. Can't disable triggers. Row-level security applies to it |
| `finpilot_backup` | Read-only for dumps, **plus `BYPASSRLS`**: `pg_dump` refuses to dump a table that row-level security would filter. Or back up as the schema owner (`PRODUCTION_READINESS.md` §2) |

Because the application role can't alter triggers, a compromised app can't remove the ledger's immutability guarantee. Erasure runs as a maintenance step under the schema owner, never from a route. See "Database roles, row-level security, export and deletion" at the end of this document.

---

## 10. Infrastructure and CI

**This section states requirements. Host-specific configuration is deliberately kept out of this public repository, and nothing here describes a system that's been hardened or deployed.**

### 10.1 CI/CD trust boundary

GitHub's security guidance says self-hosted runners "should almost never be used for public repositories", because anyone can open a pull request and compromise the environment, and that they "can be persistently compromised by untrusted code in a workflow".

**Trust boundary: no code from a pull request ever executes on a deployment host.** A host only ever runs an image built from reviewed code on `main`.

| Zone | Trust | What runs there |
|---|---|---|
| Fork or feature pull request | **Untrusted** | GitHub-hosted runners only; no secrets |
| `main`, after review | Trusted code | GitHub-hosted build; image pushed by digest |
| Deployment host | **Protected** | Only pulls a specific approved image. Never builds, and never checks out code |

**Deployment model [OD-25]:** pull-based deployment is recommended. A small script on the host checks for an approved release, verifies its digest, and deploys, so no CI job ever executes on the host.

**Safeguards:**

1. Don't use `pull_request_target` with a checkout of PR code.
2. Pin third-party Actions to full commit SHAs, and set default `GITHUB_TOKEN` permissions to read-only.
3. Protect `main`: require pull requests and passing checks, and block force pushes.
4. Deploy only an approved release, from a signed image.
5. Enable secret scanning and two-factor authentication on the GitHub account.

### 10.2 Application isolation

| Area | Requirement |
|---|---|
| **Between applications** | Own Docker network, compose project, and file ownership. Nothing shared with other software on the host except the entry proxy |
| **Docker permissions** | Non-root users, `no-new-privileges`, all capabilities dropped, read-only root filesystem where possible, default `seccomp` profile. **No container gets the Docker socket** |
| **Database isolation** | Dedicated database and least-privilege roles (OD-14) |
| **Secrets** | Per-service, outside the repository and outside images, with a written rotation procedure |
| **Public exposure** | Only `/api/v1/*` and the web app are public. Health endpoints reveal no details. The database and administrative tools have no public port |
| **Reverse proxy** | TLS, HSTS, request-body limit, slow-client timeouts, connection limits, per-IP rate limits |
| **Resource limits** | `mem_limit`, `cpus`, and `pids_limit` on each container, set from measurement |

### 10.3 Public ingress [OD-20]

A decision on how public traffic reaches the host is required before any deployment. The trade-off is between exposing a host directly, and placing a third party in the data path, which must then be disclosed in the privacy notice if it can see traffic.

### 10.4 Backups

| Element | Requirement |
|---|---|
| **Copies** | Production database, plus at least one encrypted off-site copy. A backup on the same disk isn't a backup |
| **Encryption** | Encrypted before upload, with a key the storage provider never sees |
| **Compromise** | Upload credentials can't delete or overwrite, so a compromised host can't destroy its own backups |
| **Key custody** | Decryption key held away from the host |
| **Scope** | Database, configuration, and the erasure log. Report files expire, so they're excluded |
| **Retention** | Disclosed in the privacy notice |
| **Restore testing** | Automated restore to a throwaway database, verifying ledger balance per currency and `Σ net = 0`, plus a periodic manual drill from off-site only |
| **Alerting** | A failed or missing backup raises an alert |

### 10.5 Monitoring and alerting

Required signals: external availability, backup failures, resource pressure, 5xx rate and latency, failed-login and registration spikes, job queue depth, TLS expiry, email bounces, and approaching capacity caps. **A monitor on the same machine can't report that machine's failure**, so at least one check must be external.

### 10.6 Exposure

Only the entry proxy faces the internet. Administrative tools aren't publicly reachable. Security updates are automatic. Disk encryption is recommended for any host holding real data.

### 10.7 Availability and honesty

A single self-hosted server has real limits, and it has one operator. The product must say so plainly. See §13.6.

### 10.8 Email delivery

**Email is no longer needed for signing in,** because Google handles identity. It's still needed for notifications and notices, such as a deletion request.

- Use a transactional provider over HTTPS (OD-6).
- Configure SPF, DKIM, and DMARC on the sending domain.
- Handle bounces and complaints, and stop sending to addresses that fail.
- **Limit per recipient, not only per sender,** so nobody can use FinPilot to flood someone's inbox.
- **Keep user-written text out of emails sent to unverified addresses,** so trip names can't be used for phishing.
- Emails contain no financial amounts by default.

---

## 11. Privacy and the Account Deletion Review [OD-3]

**This isn't legal advice, and FinPilot claims no compliance with any law.** This review identifies risks for a qualified professional to check. **A legal review before public launch is mandatory (§13.1).**

### 11.1 Status and sources

| Item | Status |
|---|---|
| Block normal self-service deletion while trip balances are open | **Confirmed product preference (OD-3)** |
| That the block is legally safe | **Unresolved. Flagged as a material risk (R-05)** |
| The safer design in §11.5 | **Proposed, and undecided (OD-18)** |

**Read for this review on 2026-11-05:** GDPR Arts. 3, 12, and 17 and Recital 26, on the gdpr-info.eu reference site, not the Official Journal. **For KVKK (Turkey's law), only a partial unofficial English translation and secondary summaries were available.** Art. 7 and Art. 11 were read in that translation, which itself says the Turkish text prevails. The 30-day response period under Art. 13 comes from a secondary source only, and **must be checked against the official text.**

**Which laws could apply?** This depends on facts that haven't been established: where the operator is based, and which users are targeted.

- **Open public registration, offered in Arabic and Turkish, widens the possible scope.**
- GDPR Art. 3(2) can reach a controller outside the EU who offers services to people in the EU, "irrespective of whether a payment" is required.
- KVKK is relevant if the service operates from Turkey or serve people there.
- Arabic-speaking users may bring other national laws into question, and **none of those were reviewed.**

### 11.2 What retained data actually is

**A renamed member isn't anonymous.** GDPR Recital 26 says that pseudonymised data which can be linked back to a person remains information on an identifiable person, and that only data where the person "is not or no longer identifiable" falls outside the principles. In a four-person trip, "Former member" paid for the dinner, and everyone knows who that was.

**Consequence: retained trip records should be treated as personal data, with a documented reason for keeping them.** Don't describe this as anonymization to users.

| Data | On completed deletion |
|---|---|
| Sessions, tokens | Immediately |
| Accounts, transactions, ledger, budgets, custom categories, links, duplicate dismissals, private audit events | Erased |
| Email, Google identity link, profile, preferences | Erased; the user row becomes a tombstone |
| Display name in trip records | Replaced with a neutral label |
| Amounts, dates, and shares in trip records | Kept, with a documented purpose and review date |
| Personal notes, dispute reasons | Replaced with a placeholder. **Descriptions may contain personal data too**, and a request could reach them |
| Trip-scoped audit events | Kept; actor pseudonymized |
| Reports | Deleted |
| Copies in other members' exports | **Can't be reached.** They hold their own copies |
| **Backups** | Age out under retention. The deleted user's data remains until then, and this must be disclosed |

**On restore:** a restored backup could resurrect deleted accounts. `erasure_log` holds only user IDs and dates. It's stored outside the backed-up database too, and re-applied after any restore.

**Retention must end:** records shouldn't be kept forever. **No retention period is proposed here,** because I found no basis for a specific number. It's one of the questions for legal review (§11.5), and its outcome becomes a scheduled purge job.

### 11.3 Three tracks

**Track 1: Normal self-service deletion.** The "Delete my account" button. Blocked while balances or pending items are open, as currently decided. The screen lists each blocking item and how to resolve it. It also tells the user about Track 2 and about their other options, so the block never looks like the final answer.

**Track 2: A legally valid erasure request.** Arrives through a form, or by any other means.

1. Recorded in `data_requests`, with a due date. Under GDPR Art. 12(3), that means "without undue delay and in any event within one month of receipt", extendable by two months for complex requests, with notice to the person.
2. Identity is confirmed.
3. **A human reviews it. The system never refuses automatically because of a debt.**
4. Everything not needed is erased.
5. If anything is kept, the person is told what and why. Under Art. 12(4), a decision not to act must state reasons and mention the right to complain to a supervisory authority and seek a judicial remedy.
6. The decision is recorded.

**Track 3: Minimal retention.** What stays after either route, per §11.2.

### 11.4 Topics evaluated

| Topic | Assessment | Status |
|---|---|---|
| **Applicable requirements** | GDPR and KVKK are plausibly relevant. Others unknown | Needs professional advice |
| **Does a debt justify refusing erasure?** | **Not automatically.** GDPR Art. 17(3)(e) has an exception "for the establishment, exercise or defence of legal claims", but FinPilot isn't a party to these debts, and whether it can rely on this for other members' potential claims is **unsettled here**. Whatever the answer, it would justify keeping the necessary trip records, not an entire account | **Unresolved** |
| **Data minimization** | The block's rationale covers trip records only. It can't justify keeping someone's unrelated bank accounts and transactions | Addressed by the personal-data erasure in §11.5 |
| **Identity anonymization** | Neutral labels are pseudonymization at best (§11.2) | Documented |
| **Historical transaction retention** | Personal ledger: no reason found to retain it after deletion. Trip records: keep the minimum, for a stated period | **Retention period unresolved** |
| **Legal retention requirements** | Bookkeeping laws generally target businesses' own records. FinPilot doesn't appear to be issuing or keeping commercial records for users, but **no retention law was checked**. Don't assume one exists, or that none does | **Unresolved** |
| **Trip balance preservation** | Yes, financially necessary: erasing a share would break `Σ net = 0` for others. Provable from `FINANCIAL_RULES.md` §9.2 | Design settled |
| **User access to personal information** | Export stays available at all times, including during a block, during grace, and after suspension where lawful. The block must **never** remove someone's access to their data | Designed |
| **Unresponsive or inactive participants** | **The strongest case against an unlimited block:** someone can be trapped by other people's silence | See §11.5, §11.6 |
| **Disputed debts** | FinPilot can't decide who's right. A debt that's in dispute shouldn't hold someone's account | Excluded from blocking (proposed) |
| **Permanently unable to settle** | Death, incapacity, lost contact. Needs a defined exit | See §11.6, OD-19 |

### 11.5 Is the block a material compliance risk? **Yes, as an unqualified rule.**

**Why it's a risk:**

- It could operate as an automatic refusal of a legal right, based on a third party's possible claim.
- It holds unrelated private financial data hostage to a trip debt, which is hard to reconcile with data minimization and storage limitation.
- It can last indefinitely because of other people's silence.
- It might look like using personal data as leverage to collect a debt that FinPilot has no role in collecting, and which it only records as *reported*.
- Terms of use can't simply waive these rights, though that needs checking.

**The block remains the recorded product behavior until a decision replaces it.** It has a legitimate purpose: stopping someone from running out on a debt, and protecting other members' records.

**Options for OD-18:**

| | A. Block as stated | **B. Scoped block** (recommended) | C. No block |
|---|---|---|---|
| Trip debts discourage leaving | Yes | **Yes** | Weakly |
| Normal deletion while balances open | Blocked | **Blocked, with a time limit** | Allowed with warning |
| Unrelated finance data | Held | **Erasable at any time** | Erasable |
| Legal request | **Must still be honored; a conflict** | Human review, no automatic refusal | Human review |
| Stuck forever because of others | **Possible** | **No** | No |
| Legal risk | **Material** | Lower; **still unverified** | Lowest |
| Added work | None now; risk later | About 3 days | None |

**Recommended design (option B), which keeps the block on normal deletion and removes its risky edges:**

1. **Normal deletion is blocked** while the user has a non-disputed non-zero net or pending items.
2. **"Erase my personal finance data" is never blocked.** It touches nothing other members rely on.
3. **The block has a time limit**: after 90 days of the first attempt **[Assumption]**, with warnings sent to the trip's members, deletion proceeds with Track 3 retention.
4. **Disputed amounts don't count** toward the block.
5. **Track 2 is always available**, and tells the person so.
6. **Self-service grace shortens to 14 days** (§18 of the financial rules, assumption #7).

**Before launch, these questions go to a qualified professional:** which laws apply, whether any deletion block is acceptable, what retention is defensible, and what the privacy notice must say.

### 11.6 Scenarios under recommended option B

| Scenario | Outcome |
|---|---|
| Debtor wants to leave and can pay | Settles, the creditor confirms, then deletes |
| Debtor paid, but the creditor never confirms | Blocked up to the time limit, and the creditor is reminded. Then deletion proceeds, and the pending record stays under the neutral label |
| Creditor wants to leave, and others owe them | Can forgive the debts if OD-19 is approved, or wait for the time limit |
| Debt in dispute | Not counted toward the block |
| A member stops responding | Their balance stays visible. Anyone whose own net depends on them is blocked only until the time limit |
| Inactive for years | An inactive-account policy is needed. **Not designed yet (R-62)** |
| Person has died, or is otherwise unreachable | A verified request to the operator, handled under Track 2. Other members may forgive their shares under OD-19 |
| Suspended abuser requests erasure | Track 2. Keep only what's justified for abuse prevention, and document it |
| Mistaken request, or hijacked account | Fresh Google sign-in, grace period, and email notice |

### 11.7 Data export

Includes profile, accounts, transactions, categories, budgets, and trip records that the user could already see, as CSV and JSON in a ZIP, delivered through the export mechanism. **It stays available throughout any deletion block.**

### 11.8 Third-party data flows

| Service | Data sent | Disclose? |
|---|---|---|
| FX provider | Currency pair and date only. **No user data** | Not needed as personal data |
| Email provider | Recipient address and message content. **No financial amounts by default**; messages say "you have an expense to confirm", with a link | Yes |
| Google | Every sign-in. FinPilot receives the subject ID, email, whether it's verified, and name. Google learns that the person signed in to FinPilot | Yes |
| Bot protection (OD-22), if adopted | Visitor's IP and browser signals, on first sign-in | Yes |
| Tunnel or CDN (OD-20) | **Potentially all traffic** | Yes, and it's a major decision |
| Off-site backup | Encrypted data only | Yes |
| Translation tools | **UI strings only; never user data** | No |

### 11.9 Operator access

The operator can technically read all stored data. The privacy notice must say so, and access should be limited in practice: no routine queries against user data, with production access logged. There is no field-level encryption in the MVP, because the server must compute with this data.

---

## 12. Security Acceptance Criteria

**Status as of the 2026-11-09 audit (`SECURITY_AUDIT.md`):** met unless noted. Open: applying the restricted database role to real databases (built and tested on an isolated server only), CI image scanning, a verified backup restore, and the runner check on the real repository.

- [x] The authorization matrix test covers 100% of routes.
- [x] A cross-user access test passes on all resource types (IDOR).
- [x] The marker-string privacy test passes for trip API responses, CSV, and PDF (§4.5).
- [ ] Former members get 404 for expenses outside their scope (§4.4).
- [x] Session tokens never appear in logs (checked by a log-capture test).
- [x] The raw-SQL test shows that the `finpilot_app` role can't update or delete journal rows or disable triggers. (On an isolated server: `tests/test_db_security.py`.)
- [x] CSRF test: unsafe cookie-authenticated requests without a token are rejected.
- [x] CSV injection test covers all trigger characters, including after invisible and bidirectional marks, in all text columns and all three languages.
- [x] PDF test: `<img src="file:///etc/passwd">` and `<script>` in a description render as literal text, with no fetch attempted.
- [x] Export download is denied for every non-owner.
- [ ] Dependency and image scanning runs in CI, with no unresolved high-severity items at launch.
- [ ] Backup restore has been performed and verified, including once from off-site only.
- [ ] The self-hosted runner can't run fork or PR workflows (verified on the actual repo), or has been removed.

---

## 13. Public Launch Requirements [OD-5 confirmed]

**FinPilot isn't ready for unrestricted public use, and this document doesn't claim otherwise.** Open registration from the initial production release is a confirmed decision. The production release may not open until every **Mandatory** item below has evidence. A portfolio demo (§13.7) doesn't need this gate, because it holds no real users' data.

### 13.1 Mandatory launch safeguards

| Area | Requirement |
|---|---|
| **Account creation abuse** | Rate limits per IP and per subnet on sign-in, a global daily cap on new accounts, and a kill switch that stops new accounts without a deploy. Google sign-in raises the cost of fake accounts, but doesn't remove it |
| **Automated bots** | Whether a bot check is still needed on top of Google sign-in is OD-22, decided from measured abuse |
| **Verified email** | Google must report it verified, or no account is created (§2.5) |
| **Rate limiting** | At the proxy per IP, and in the application, on all endpoints (`API_SPEC.md` §15). Trusted-proxy configuration, so the real client IP is used. **Built so far: an in-process limiter on sign-in and account changes. It counts per worker and resets on restart, so it isn't enough for production; production needs the shared `rate_limit_counters` table or proxy limits** |
| **Sign-in flooding** | Rate limits on the start and callback routes, and expiry-based cleanup of unfinished flows. There's no password to brute-force |
| **Session revocation** | User-driven (§2.2), and on suspension and deletion. Takes effect on the next request |
| **Resource exhaustion** | Request-body and upload limits, timeouts, connection limits, query timeouts, and the per-user quotas in §13.2 |
| **API abuse** | Pagination maximums, allowlisted sort fields, cost-aware limits on reports, and bounded FX lookups |
| **Files and reports** | §7, plus the limits in `ARCHITECTURE.md` §6.4, a bounded global queue, and disk-usage monitoring |
| **Database connections** | Fixed pool sizes that sum to less than the role's `CONNECTION LIMIT`. `statement_timeout` for requests, and `idle_in_transaction_session_timeout`. Saturation returns 503 instead of hanging |
| **Monitoring and alerts** | §10.5, including one external check |
| **Backup and recovery** | §10.4. At least one verified restore from off-site before launch |
| **Abuse handling** | `suspended` status, an operator procedure, an abuse-contact address, and audit of operator actions |
| **Security contact and incident plan** | A `security.txt` file, a written breach-response plan, and a way to notify all users |
| **Legal pages** | Privacy notice, terms of use, and cookie or storage disclosure, **in English, Arabic, and Turkish**, written or reviewed with professional advice. Acceptance recorded per version. Terms state that FinPilot is not a bank, doesn't move money, and records settlements only as reported |
| **Legal review** | The questions in §11.5 answered, and OD-18 decided |
| **Age and eligibility** | A stated minimum age, per legal advice |
| **Operational honesty** | A status page or notice, and plain language about home hosting, no SLA, and backup limits |
| **Capacity cap** | A global user cap, set from load tests on the real machine. Above it, registration closes with a clear message (§13.5) |
| **Hosting conditions** | Every condition in §13.6 passes on the real server, and OD-20 is decided. **Explicit owner approval is recorded** |
| **Security testing** | The criteria in §12 pass, plus an external-style review. For a solo project this can be a checklist-based self-assessment with automated scanning. **It must be described honestly as such** |

### 13.2 Per-user quotas (initial; tune from measurement)

| Resource | Limit |
|---|---|
| Financial accounts | 50 |
| Custom categories | 100 |
| Transactions | 50,000 total; 1,000 per day |
| Active trips owned | 20 |
| Members per trip | 50 |
| Expenses per trip | 5,000 |
| Open invitations per trip | 20; 50 per day per user |
| Manual rates | 200 per day |
| PDF exports | 10 per hour; 30 per day |
| CSV exports | 30 per hour |
| Data exports | 2 per day |
| Text fields | Length limits on every field |

**Exceeding a quota returns a translated 429 with the limit explained.** It never truncates data silently. Quotas are generous for real people and bound what a single abusive account can cost.

### 13.3 Report and export abuse

- **Global queue bound:** above 20 queued PDFs, new requests get 429, with an honest "busy" message.
- **Fair use:** one active export per user, so one user can't fill the queue.
- **Render limits:** child process with time and memory caps (`ARCHITECTURE.md` §6.4).
- **Disk bound:** a total cap on the export volume; the oldest ready files are deleted first if it's reached.
- **FX lookups:** only supported currency pairs and valid dates, so nobody can drive unbounded outbound calls or table growth. A global outbound budget, and caching of "no rate" results. As built in M5: 60 outbound calls a minute per process, failures cached 60 s and unsupported pairs 24 h, `fx_rates` grows by at most one row per provider, pair and source date, and the preview and equivalents endpoints are limited to 60 requests a minute per client.

### 13.4 Optional future enhancements

**Valuable, but not launch blockers:**

| Enhancement | Note |
|---|---|
| **TOTP two-factor authentication** | First priority after launch, for an app holding financial records |
| Passkeys | |
| New-device login notifications | Cheap and useful |
| Web application firewall rules | Depends on OD-20 |
| Paid external penetration test | Worthwhile if it gains real traction |
| Self-hosted error tracking | |
| Status page on separate infrastructure | |

### 13.5 Capacity

**Earlier documents assumed "a few dozen active users." Open registration removes the guarantee that this stays true.**

The answer isn't to design for scale that may never come. It's to **know the limit and fail honestly**: measure at PD4, set the global cap below the measured limit, alert at 80%, and close registration when reached. Existing users are never affected by the cap.

### 13.6 Operational limits of a single server

The planned deployment is one self-hosted server with one operator [OD-21].

| Limit | Effect on public users | Mitigation |
|---|---|---|
| One machine | Hardware failure means an outage | Tested rebuild runbook; off-site backups |
| Power and network | Unplanned outages | Honest messaging, no SLA |
| One operator | Slower response | Automation, alerts, and a read-only mode |
| Shared hardware | Resource contention, and a wider blast radius | Resource limits and isolation |

**Production hosting conditions. All must pass on the real host before public registration opens, and this approval is conditional on them:**

| # | Condition |
|---|---|
| HC-1 | A pre-deployment host inspection is complete, with no unresolved failure |
| HC-2 | Enough memory and disk under measured load |
| HC-3 | A written isolation review, covering networks, credentials, and file ownership |
| HC-4 | No untrusted code can reach the host (§10.1) |
| HC-5 | Public entry exposes no administrative tools; OD-20 is decided |
| HC-6 | Encrypted off-site backups exist, and one restore from off-site has been done |
| HC-7 | Hosting a public service is permitted by the network provider's terms |
| HC-8 | A written disk encryption decision |

**If a condition can't be met, public registration stays closed.** The design doesn't depend on where it runs, so moving remains possible.

### 13.7 Demo environment

The demo has its own requirements, in §14. **It doesn't satisfy, replace, or count toward anything in this section.**

---

## 14. Portfolio Demo Requirements [OD-24 approved]

**The Portfolio Demo is a limited, working application with synthetic data. It isn't a financial service for real data, and it isn't evidence of production readiness.** Its authentication, ledger, and authorization are real.

### 14.1 Demo design

| Area | Design |
|---|---|
| **Data** | Synthetic only, generated by a seed script. No real names, no real financial records |
| **Accounts** | **Per-visitor sandbox.** "Try the demo" creates a private copy of the seed data, so one visitor's text is never shown to another. In Phase B, a sandbox has several synthetic personas, so a visitor can experience confirming as the payer |
| **Authentication** | **No Google sign-in.** "Try the demo" creates a sandbox and a real FinPilot session for one of its synthetic personas. This route exists only in demo builds, never in production. Real sessions and CSRF, and persona switching only inside that sandbox |
| **Authorization** | The same server-side checks as production. A cross-sandbox test is required |
| **Google sign-in, email** | **Off in the demo, and absent from its UI and routes.** Settings refuse a demo with Google configured. There are no disabled buttons or fake forms |
| **Reset** | The whole demo database is wiped and reseeded nightly. Visitors see the next reset time |
| **Notice** | A visible banner: *"Demo with synthetic data. It resets nightly. Don't enter real information."* |
| **Claims** | The demo never describes itself as secure for real data, or as a live service |

### 14.2 Demo isolation

| Area | Requirement |
|---|---|
| **Containers** | Separate compose project and Docker network. Non-root, `no-new-privileges`, capabilities dropped, read-only filesystem where possible. No Docker socket |
| **Database** | Its own small PostgreSQL container, on an internal network with no published port (OD-14). It can't reach any other app's database |
| **Other applications** | No shared volumes, networks, or credentials. **No access to other apps' private data** |
| **Secrets** | Unique to the demo. Never reused for production |
| **Filesystem** | No host mounts, beyond what the reset job strictly needs |
| **Outbound** | Only the FX provider, in Phase B. No email |
| **Later production** | Fully separate credentials, database, and network |

### 14.3 Demo limits (initial; estimates until measured)

| Resource | Limit |
|---|---|
| New sandboxes | 5 per IP per hour; 200 per day overall |
| Requests | Proxy limit per IP, plus 120 per minute per session |
| Records per sandbox | 500 transactions, 10 accounts, 5 trips, 100 expenses |
| Text fields | Normal length limits |
| Memory | API 256 MB, web 256 MB, database 256 MB |
| Uploads, exports | None in Phases A and B |
| On abuse | Stop the demo with one documented command. Other software on the host is unaffected |

### 14.4 Demo deployment gate

**The demo may not be published until each item has evidence.**

| # | Requirement |
|---|---|
| DG-1 | **Explicit owner approval** |
| DG-2 | The pre-deployment inspection (`ARCHITECTURE.md` §12) is complete, and shows capacity and no blocking issue |
| DG-3 | **No untrusted GitHub Actions on this host.** CI runs on GitHub-hosted runners. OD-25 is decided |
| DG-4 | The isolation in §14.2 is verified on the real host, including that demo containers can't reach other apps' networks |
| DG-5 | Public entry is decided (OD-20) and doesn't expose anything else on the host, or administrative tools. TLS works |
| DG-6 | Rate limits and the limits in §14.3 are active, and tested |
| DG-7 | Automated financial and cross-sandbox authorization tests pass in CI on the deployed version |
| DG-8 | Only synthetic data is present; a scan of the seed confirms it. The banner is visible |
| DG-9 | Demo routes for production-only flows are absent, with no nonfunctional controls |
| DG-10 | Monitoring with resource alerts. Logs follow §8 |
| DG-11 | A smoke load shows other software on the host is unaffected at the demo's limits |
| DG-12 | The stop and removal procedure has been run once |

**Backups aren't required for the demo,** since its data is disposable. The code and seed live in the repository.

**If the gate can't be met,** the demo isn't published on this server. A recorded walkthrough and locally run demo remain available for applications.

### 14.5 What the demo doesn't prove

Passing §14.4 says nothing about open registration, email, abuse at public scale, real-data privacy, backups, or legal obligations. **Those remain in §13, and all of §13 still applies to the production release.**

## M6.1 export review (2026-11-07)

- CSV: UTF-8 with BOM, RFC 4180 quoting (commas, quotes and newlines round-trip, tested), formula neutralisation (tested), signed amounts with debts signed as the app shows them (lending and repaying negative), user-local dates and `recorded_at` in the user's time zone, explicit 50,000-row limit (413).
- PDF: see ARCHITECTURE.md "M6.1" for the renderer controls (JavaScript off, network blocked, escaping, timeouts, bounded concurrency, guaranteed cleanup, no temporary files).
- Home equivalent: a read; no record is written apart from the reference-rate cache.

## Trip Mode T1 (2026-11-09)

- Privacy boundary enforced by an import-linter contract: `app.services.trips`, `trip_expenses` and `allocation` can't import any personal-finance module. A test plants a marker string in a member's private account and checks no trip response (trip, members, expenses, dashboard, activity) contains it, nor the member's email or balance.
- Member responses carry only `user_id`, `display_name`, `role`, `status`.
- Invitation tokens: 256-bit, shown once, stored as SHA-256; the link carries the token in the URL fragment (`/{locale}/invite#token`), which the web app moves into sessionStorage and removes from the address bar; the invite page is no-store and no-referrer.
- Only the payer approves or rejects (403 `not_payer` for the owner and admins, tested). The per-endpoint authorization matrix test (§4.7) is not complete yet.

## Trip Mode B4 (2026-11-10)

- **Credential incident closed.** The development role `finpilot_dev_user` password, exposed in a session's output on 2026-11-09, was rotated with `backend/scripts/rotate_dev_password.py`: the role changes its own password (no administrator needed), the new password is sent only as a SCRAM-SHA-256 verifier, written only to `backend/.env` (DATABASE_URL line), and never printed. The old credential is refused; the app reconnected. No other role or database was touched.
- The **per-endpoint authorization matrix** (§4.7) now exists: every trip and invitation route must have an entry (a new route without one fails the build); each is checked as anonymous (401), never-a-member (404), plain member on admin routes (403), admin on owner routes (403).
- Nobody confirms a payment or resolves a dispute for someone else; owners and admins have no override. Trip modules (now including settlements and disputes) still can't import personal-finance code.

## Trip Mode B5 (2026-11-11)

- **Links are owner-only.** Every link, dismissal and position is looked up with the session's user (404 otherwise); the transaction foreign key includes the owner, so a link can't point at someone else's transaction. Linking needs CSRF and an Idempotency-Key, locks the trip item row and the account, and is one database transaction.
- **Import contracts:** the bridge `services.trip_links` is the only module that reads both sides; trip modules (now including `trip_reports`) may not import it or any personal-finance module, and a new contract forbids the trip routes (`api.v1.trips`, `api.v1.trip_reports`) from importing links, ledger, transactions, accounts, reporting or budgets directly. Three contracts, all kept.
- **Tested:** marker strings, link and transaction ids and balances never appear in any trip page, file or report, for the owner or another member; the permission matrix covers the five report routes; cross-user link access is 404; concurrent links post once.
- The browser check used `finpilot_test` only, with the suite's fake Google and fake rates; test sessions only.

## Everyday milestone (2026-11-12)

- Every new route is owner-scoped (404 for someone else's template, goal, group, transaction or link), needs CSRF for changes, an Idempotency-Key for creates and money changes, and If-Match for edits. Refunds and one-step links lock the rows they depend on (purchase; trip item and account), so concurrent requests can't double-post (tested).
- One-step linking lives in the personal links API, so the trip routes still import no personal code (three import contracts, kept). Trip members never see the account, the amount charged, or whether a link exists (tested on every trip page and report).
- Goal history is append-only in the database itself. Templates never post money.
- Browser checks used finpilot_test only, with the suite's fake Google and fake rates.

## Expansion milestone (2026-11-14)

- **Private trip budgets:** `trip_personal_budgets` rows are only ever read and written for the
  caller (`(trip_id, user.id)`); there's no route or field that exposes another member's. Covered by
  `test_planning.py` and a browser test.
- **Trip plans** are visible to current members only (404 to anyone else); an item is changed by
  whoever added it or an admin. Links must be https and open with `noopener noreferrer`. No
  sensitive travel identifiers are collected (reference codes only; no passport or card fields).
- **Every new trip route** is in the permission matrix (`test_settlements.py`), checked for
  anonymous, unrelated, member and admin callers.
- **Installment plans and totals** are per user (404 otherwise); payments are idempotent; changes
  need If-Match.

## Security audit (2026-11-09)

Full findings, verified behaviour, and open decisions: [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md).

- **Rate limits** count each signed-in person; before sign-in they count the client address, which needs trusted-proxy configuration in production. Expensive reads have their own ceiling.
- **Every API response** carries `no-store` unless a route says otherwise, `nosniff`, frame denial, and, outside local development, HSTS. API documentation is served only in development and test.
- **Input:** values the database or encoder can't hold are refused with 422. Dates are bounded to 1900 to 2199. Requests declaring more than 1 MiB are refused.
- **Shared trip text** can't contain direction overrides or control characters. Joiners used by Arabic, Persian and emoji are allowed.
- **Guards over routes** walk the real route tree and fail if they find none.

## Database roles, row-level security, export and deletion (2026-11-09)

**Status: built and tested on isolated databases only. `finpilot_dev` has not been changed**: not its roles, ownership, privileges, schema or policies.

### Roles

The app needs a runtime role that owns nothing. `app/db/hardening.py` is the single source for what that role may do, and `scripts/db_hardening.py` applies and checks it. It's deliberately outside the Alembic chain, so `alembic upgrade head` can't apply it by accident.

| | Schema owner | Runtime role |
|---|---|---|
| Runs | Migrations, maintenance, erasure | The application |
| Owns tables | Yes | **None** |
| DDL, triggers, constraints | Yes | **Refused** |
| Row-level security | Doesn't apply (`FORCE` is off, on purpose) | **Applies, and can't be bypassed** |
| Journal and audit history | | Insert and read only |

To roll it out, once approved:

1. Back up the database.
2. `create-role` as an administrator, with `RUNTIME_ROLE_PASSWORD` set. The script sends only a SCRAM verifier.
3. `apply --database NAME` as the schema owner, after migrations. Anything but `*_test` also needs `--owner-approved`.
4. `verify` as the runtime role. It tries each forbidden action in a transaction it rolls back.
5. Only then point the app's `DATABASE_URL` at the runtime role, and keep the owner's URL for migrations.

In production, `/readyz` fails if the app connects with a privileged role, owns tables, or finds RLS missing.

### Row-level security

Each transaction sets `app.user_id` from the validated session (`app/db/rls.py`). It's transaction-local, so it can't leak through the connection pool.

- **Personal tables:** only the owner's rows, for every command.
- **Trip tables:** any member, current or former, can read. Adding expenses, plan items or invitations needs an active member. A former member can still settle up or respond, because the app allows that.
- **Invitations:** a non-member sees one invitation and its trip only while holding that token's hash.
- **No RLS, each for a stated reason** (listed in `hardening.py`): `users`, `user_identities`, `sessions` and `oauth_states` are needed before anyone is identified; `audit_events` is written during sign-in and about other people's sessions; `currencies` and `fx_rates` are shared reference data.
- A new table with no decision makes `apply` refuse, and a test fails.

**What RLS does not do.** It catches a query that forgot its `user_id` filter. It doesn't stop arbitrary SQL injection, because the runtime role sets `app.user_id` itself. What a former member sees *within* a trip is narrower than RLS and stays enforced by the services (§4.4).

**Background work:** there are no background jobs today. Anything added later must set an identity or run as the owner, knowingly.

### Personal data download

`GET /api/v1/me/data-export`: a JSON attachment with a date-only filename, `no-store`, five an hour per person, and at most `data_export_max_rows` rows per section. If anything is cut, `complete` is false and the file names the section. Trip records are reduced to the person's own part: no other member's name, email, ID or budget, and only notes they wrote. It stays available during a deletion block and while a deletion is pending.

### Account deletion

| State | How it's reached | What it means |
|---|---|---|
| `active` | | Normal |
| `deletion_pending` | `POST /me/deletion-request` | Reversible. Read and download only; other browsers signed out |
| `active` | `DELETE /me/deletion-request` | The person changed their mind |
| `deleted` | Maintenance only | Irreversible |

A request needs the account's email typed out, an acknowledgement of what stays, and a Google sign-in within 15 minutes. The row is locked, so concurrent requests succeed once. Every transition is audited.

**Blocks:** a non-zero trip balance, pending trip items, an open dispute, or owning an open trip that others are on. This is the recorded behaviour (OD-3), implemented as stated, and still subject to OD-18 (§11.5). **Personal debts and installments only warn:** they're the person's own records, and §11.4 finds no basis for holding them.

**Erasure** (`app/services/account_erasure.py`) follows §11.2. No route reaches it. `scripts/erase_account.py` runs it as the schema owner, needs an explicit grace period, re-checks blockers, and **refuses every database not named `*_test`, with no override.** Lifting that is a deliberate code change, to be made once retention is decided.

### Decisions still open

- OD-18: keep the block as stated, or scope it (§11.5), including whether disputed amounts count.
- The grace period. The docs recommend 14 days as an assumption; nothing enforces one yet.
- How long retained trip records are kept. No period is proposed (§11.2).
- Who operates erasure requests, how to reach them (Track 2), and the privacy notice. The page refers to "the operator" without a contact.
- `erasure_log`, for re-applying erasures after a restore from backup, isn't built.
- Legal review before launch (§11).

## Production packaging (2026-11-12)

Details: [`deploy/README.md`](../deploy/README.md) and `PRODUCTION_READINESS.md` (status after P2a). **Built and statically checked; not yet run in containers.**

- **PDF sandbox (a fix):** Playwright launches Chromium with `--no-sandbox` unless told otherwise, so until now PDF rendering ran unsandboxed, despite §7.2 and the M6.1 notes. `exports.py` now passes `chromium_sandbox=True`, and there is no setting to turn it off. If the sandbox can't start, the export answers 503.
- **Trusted proxy:** `TRUSTED_PROXIES` lists the reverse proxy's address. Only its `X-Forwarded-For` is believed, and only the last untrusted entry counts. Production refuses to start without it, or with anything wider than a /8. Uvicorn's own proxy handling is off.
- **Secrets:** in containers every secret is a file under `/run/secrets`, given only to the containers that need it. None is an environment variable, so none shows in `docker inspect`. `read_secret` (`app/core/config.py`) gives the scripts the same behaviour.
- **Migrations** use the schema owner's `MIGRATION_DATABASE_URL`. In production they refuse to fall back to the app's runtime role.
- **Provisioning:** `db_hardening.py create-owner` creates an owner with no server-wide rights, and its database. It never takes over a database owned by another role, and changes nothing that already exists.
- **Containers:** non-root users, read-only filesystems, all capabilities dropped, `no-new-privileges`, resource limits, rotated logs, and no Docker socket. Only the proxy publishes a port, on loopback unless configured. The database and web server have no internet access.
- **Proxy logs** drop query strings (the Google callback carries `code` and `state`), headers and cookies, and shorten client addresses.
- **Backups** are dumped as the owner, because a role bound by RLS can't dump, and are encrypted with `age` before they touch the disk. Restore only ever writes into a new database.

## Integration-test preparation (2026-11-13)

- **Chromium sandbox in containers.** Docker 29.9.2's default seccomp profile allows namespace creation (`clone` with namespace flags, `unshare`) only with `CAP_SYS_ADMIN`, and `chroot` only with `CAP_SYS_CHROOT`. FinPilot grants neither.
  - The API container therefore has its own profile: Docker's default plus exactly those three calls, generated reproducibly from Docker's file (`deploy/seccomp/`). No capability is added, and every other container keeps the default.
  - Trade-off: processes in the API container can create user namespaces, a larger kernel surface than the default profile allows. It's accepted so Chromium's renderer sandbox stays on. A separate rendering container is the later improvement.
  - `scripts/sandbox_probe.py` proves the sandbox from `/proc`: no `--no-sandbox` flag, and each renderer in its own PID namespace under its own seccomp filter.
- **Client addresses behind two proxies.** In behind mode each hop trusts exactly one address: cloudflared, the existing Caddy (`FINPILOT_EDGE_IP`), then the FinPilot proxy (`TRUSTED_PROXIES`). Every hop forwards a single `X-Forwarded-For` value, `{client_ip}`, and the FinPilot proxy reads right to left (`trusted_proxies_strict`). A visitor's own header is never passed on.
- **Container user IDs** are unique on the home server: api 10201, web 10202. Before, web ran as 1000, the host administrator's ID, and the API as 10001, the AI app's ID, so a container escape would have landed as those users.
- **Backups** stream straight into restic, encrypted on the client. A dump that fails part-way leaves no snapshot behind.

## Closed beta and automatic deployment (2026-11-13)

- **Admission.** A verified Google address alone no longer creates an account. Production defaults to `REGISTRATION_MODE=allowlist`, with the addresses in a secrets file, and refuses to start with `open` or with an empty allowlist. A refused sign-in creates nothing; the audit log records only the reason, never the address. Existing accounts always sign in, subject to their status.
- **CI/CD trust model.** Releases build and test on GitHub-hosted runners, only after CI passes for a push to `main` in this repository, with `packages: write` for that one job. The server pulls the images. It accepts only `ghcr.io/hishamfekri/finpilot-{api,web}@sha256:` digests from a manifest that it validates line by line and never runs. Its own scripts are root-owned copies from a commit the owner installed, and they never update themselves. No self-hosted runner exists.
- **Data safety in automation.** Every deployment backs up before migrating. A release whose new `upgrade()` steps drop, truncate, delete, update or retype data waits for an explicit approval file. The first deployment refuses to run if a database volume already exists.
