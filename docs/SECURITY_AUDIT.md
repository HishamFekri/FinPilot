# FinPilot security and privacy audit

**Date:** 2026-11-09. **Scope:** the local repository and test database only. Nothing was deployed, and no real Google account, production system, or external service was probed. Secrets were compared by match and never read out.

This is a self-assessment with automated tooling. It reduces risk; it doesn't prove the application is secure, and it is not an independent penetration test.

## Threat model

**Assets:** personal transactions, balances, income and spending history; trip expenses, member identities, and private trip budgets; sessions; exports; preferences; the financial audit history.

| Trust boundary | Who | Enforced by |
|---|---|---|
| Anonymous | Anyone | Only sign-in, health and logout answer without a session |
| Authenticated person | One Google identity (`sub`) | Every query scoped to `user.id`; another person's record is "not found" |
| Trip member | Invited and accepted | Membership check on each trip route; shared trip data only |
| Trip admin, owner | Roles on a trip | Permission matrix (SECURITY.md §4.3) |
| Former member | Left or removed | Limited read of what concerns them; no new expenses |
| Test and demo | Separate configuration | Test database guards; the demo can't use Google sign-in |
| Internal services | Trip modules vs personal finance | Import contracts forbid trip code reading ledgers or private links |

**Main threats considered:** one person reading or changing another's data (IDOR); cross-site requests; session theft or fixation; OAuth login CSRF and open redirects; a trip member reaching private money; double-posting through retries or races; injection through text, CSV, or PDF; resource exhaustion; secrets in code, bundles, or logs.

## Findings

No Critical findings. **No cross-user data exposure was found** in any sweep.

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | **High** (control failure) | Two guards, "every route needs a session" and "every change needs CSRF", had silently stopped checking anything: after a FastAPI upgrade `app.routes` no longer lists included routes. No route was actually unprotected, but a future one would have passed. | **Fixed.** The guards walk real routes, and a test fails if the walk finds none |
| 2 | Medium | CSV and PDF exports had no `Cache-Control`: routes set it through a dependency that a directly returned file doesn't inherit. Financial files could stay in a browser or shared cache. | **Fixed** centrally, for every response |
| 3 | Medium | Rate limits counted the connecting address. Behind the web app's proxy, that is one address for everybody, so one person could use up everyone's limit. | **Fixed for signed-in people** (per person). Sign-in routes need trusted-proxy configuration |
| 4 | Medium | Text other trip members read allowed bidirectional override characters, which can make text or an amount read differently from what it says. | **Fixed.** Arabic, Persian joiners, emoji and Turkish are kept |
| 5 | Low | Hostile input caused server errors instead of refusals: NUL characters, lone surrogates, characters refused inside services, and extreme dates and months. No data was exposed. | **Fixed** |
| 6 | Low | API documentation (`/docs`, `/openapi.json`) was published in every environment. | **Fixed:** development and test only |
| 7 | Low | API responses lacked `nosniff`, frame and transport headers; the web app lacked HSTS and COOP. | **Fixed** |
| 8 | Low | No application limit on request size. | **Partly fixed:** requests declaring over 1 MiB are refused. Chunked bodies need the proxy's limit |
| 9 | Low | Expensive reads, and three write routes, had no rate limit. | **Fixed** |
| 10 | Info | The installment preview skipped CSRF. It changes nothing. | **Fixed** for consistency |
| 11 | Low | No `idle_in_transaction_session_timeout`, which SECURITY.md §13.1 requires. | **Fixed** |
| 12 | Low | CSV cells beginning with a tab or carriage return weren't neutralized: the filter for invisible characters removed them before the check. | **Fixed** |

## Verified secure behaviour (tests run)

- **OAuth:** state is single-use, expires, and is bound to the browser; PKCE; nonce; signature, issuer, audience and expiry checks; RS256 only; identity by Google `sub`, never by email; no open redirects; session rotated at sign-in.
- **Sessions and cookies:** opaque tokens stored only as hashes; idle and absolute expiry; revocation on the next request; `HttpOnly` session cookie; `SameSite=Lax`; `Secure` and `__Host-` outside local development.
- **CSRF:** all 90+ changing routes refuse a missing token, a wrong token, a cross-site origin, a missing origin, and a `null` origin.
- **Isolation:** all 171 route operations refuse anonymous callers. A stranger using another person's IDs in the path, body or query gets nothing, and "theirs" is indistinguishable from "doesn't exist". Ownership fields can't be smuggled into a body.
- **Trips:** the permission matrix covers every trip route. A member or former member never receives another member's accounts, transactions, goals, private budget, or email. Invitations are 256-bit, hashed, expiring and single-use; the token stays in the URL fragment, out of requests.
- **Money:** one Idempotency-Key posts once under concurrency for expenses, transfers, installment payments and trip expenses. Racing payments can't overpay, racing voids and reversals apply once, racing acceptances admit one person. The journal and audit history are append-only in the database.
- **Injection:** parameterized SQL throughout; no shell execution or unsafe deserialization; React escapes output and no raw HTML is rendered; stored markup doesn't run in the browser; trip links must be https.
- **Exports:** formula injection neutralized, including in text from other members; PDF templates autoescaped; JavaScript off, network blocked, timeouts and a concurrency cap on the renderer; row caps; explicit partial-report labels.
- **Secrets:** no real secret value is in any repository file, log, or browser bundle.
- **Dependencies:** no known vulnerabilities in backend or production frontend packages.

## Partly mitigated

- Rate limits are per process and reset on restart. Several API workers would each count separately.
- Sign-in rate limits use the connecting address until trusted-proxy handling is configured.
- The request size limit relies on the declared length, so the reverse proxy must also limit bodies.

## Not tested

Real Google sign-in; the production reverse proxy, TLS and hosting; backups and restore; a physical phone; screen readers; load and capacity; an independent external review.

## Risks that need deployment configuration

1. Run the API behind the reverse proxy with `--proxy-headers` and `--forwarded-allow-ips` set to that proxy only, so the real client address is used.
2. Set a request body limit and connection limits at the proxy; terminate TLS and redirect HTTP.
3. Use `APP_ENV=production`, with separate database, Google client and secrets from development.
4. Serve the web app and API from one origin, as the settings already require.
5. Keep Chromium's sandbox on for PDF rendering, and give the PDF process limited memory.
6. Run one API worker, or move rate limiting to a shared store.

## Actions needing the owner's decision

1. **Applying the restricted database role.** Built and verified on an isolated server. The development database still connects as its owner, so a future SQL injection there could disable the ledger's triggers. Applying it needs a new role and credentials.
2. **Applying row-level security.** Built and verified on an isolated server; not applied to any real database.
3. **Account deletion policy.** Data download and the reversible deletion request are built. Final erasure is built but limited to test databases until the retention questions in SECURITY.md §11 are answered.
4. **Legal pages, abuse handling, per-user quotas, new-account cap** (SECURITY.md §13) aren't built and are required before public launch.
5. **Development tooling advisories:** 13 low or moderate advisories, all in development-only packages (the `shadcn` CLI and the ESLint toolchain). The proposed fixes are breaking major changes, so they weren't forced. None ships to users.
6. **Personal text fields** still allow some invisible characters where they're only shown to their owner. It's a low risk, left as is.

## Follow-up: production readiness (2026-11-12)

The deployment-side review (proxy, client addresses, backups with RLS, beta access, erasure for real data, legal pages) is in [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md), classified into launch blockers, pre-beta work and optional improvements.
