# FinPilot — Decisions and Risks

**Status:** Planning document, updated as the project develops

This register records engineering decisions and design risks. **Host-specific operational risks are tracked privately, so finding numbers have gaps.** Nothing here claims legal or security compliance, and no entry means a risk has been eliminated in running software unless it says so.

---

## 1. Approved Decisions

| ID | Decision | Where |
|---|---|---|
| **OD-1** | Record actual cash; separately represent allocation and outstanding reimbursements | `FINANCIAL_RULES.md` §11, §20 |
| **OD-2** | Beneficiaries may dispute. No automatic balance change | `FINANCIAL_RULES.md` §12.8 |
| **OD-3** | Normal deletion is blocked while trip balances are open. **Its legal safety is unresolved: see OD-18** | `SECURITY.md` §11 |
| **OD-4** | All members see all trip expenses. Private accounts stay private | `SECURITY.md` §4.4, §4.5 |
| **OD-5** | Open registration at the public release, only after the launch gate | `SECURITY.md` §13 |
| **OD-7** | One currency per budget, with exclusions listed | `FINANCIAL_RULES.md` §6 |
| **OD-8** | Combined reporting currency deferred. Trip base currency unaffected | `FINANCIAL_RULES.md` §16.4 |
| **OD-9** | Closed: English, Arabic, and Turkish are all confirmed | `ARCHITECTURE.md` §14 |
| **OD-10** | Budgets use the allocated share of approved expenses | `FINANCIAL_RULES.md` §6, §20.2 |
| **OD-11** | Portfolio-first delivery: Phases A, B, C, D | `ROADMAP.md` |
| **OD-12** | **Google OAuth is the only sign-in method,** for web, production, and future mobile. No passwords. Identity is Google's `sub`; email never identifies or links anyone. **Supersedes the earlier email-and-password scope** | `SECURITY.md` §2, `ARCHITECTURE.md` §5.3 |
| **OD-16** | Allocations derived from trip records, and not posted to the ledger | `FINANCIAL_RULES.md` §11.2 |
| **OD-17** | Allocated shares count as personal spending. Report filters provided | `FINANCIAL_RULES.md` §11.10 |
| **OD-21** | A single self-hosted server, conditional on inspection and review | `SECURITY.md` §13.6 |
| **OD-24** | A separate demo environment with synthetic data | `SECURITY.md` §14 |

---

## 2. Open Decisions

| ID | Decision | Decide by | Blocks |
|---|---|---|---|
| **OD-6** | Email provider, for notifications only. Sign-in doesn't need it | Before the first real email | Notifications |
| **OD-13** | Cover images: presets or uploads | Before C3 | Trip completion |
| **OD-14** | Database placement | Before deployment | Deployment |
| **OD-15** | PDF engine, from a rendering spike | Start of Phase C | PDF export |
| **OD-18** | Account deletion design, with legal advice | Before PD2 | **Public production** |
| **OD-19** | Debt forgiveness | Before C3 | Trip completion |
| **OD-20** | Public entry point | Before deployment | Deployment |
| **OD-22** | Whether a bot check is still needed on top of Google sign-in | Before PD1 | **Public production** |
| **OD-23** | Digits used in the Arabic UI | Before C7 | Localization |
| **OD-25** | Deployment workflow | Before deployment | Deployment |
| **OD-26** | Closed beta admission: sign-up mode, allowlist, cap and kill switch. Recommended: allowlist, cap 25 (`PRODUCTION_READINESS.md` §4.1) | Before the beta | **Closed beta** |
| **OD-27** | Version control and a build source for images. Recommended: Git with a private remote before the private deployment | Before deployment | Deployment |

Recommended defaults for OD-14, OD-20 and OD-25 are in `PRODUCTION_READINESS.md` §9.

**Identity questions, for the next A2 steps to settle:**

| Question | Current position | Decide by |
|---|---|---|
| Rate limits for production | The in-process limiter counts per worker and resets on restart. Production needs the shared table or proxy limits | Before public production |
| Content Security Policy | **Settled in A2.5:** nonce-based, with no violations in a production build. One exception: inline style attributes (`ARCHITECTURE.md` §5.4) | Done |
| Recent sign-in for deletion and erasure | **Tested (A2.4):** Google sends no `auth_time` and doesn't enforce `max_age`. Proposed: a FinPilot session issued within 15 minutes, plus typed confirmation. No re-authentication for ordinary actions or export (`SECURITY.md` §2.3) | Owner confirms before C1 |
| Recovery after permanent loss of a Google account | No self-service path. A manual, evidence-based operator process, never email alone | Before public production |
| When terms are accepted | Proposed: right after the first sign-in, before the app can be used. **Not built in A2.5:** no terms text exists yet | Owner, before public production |
| Cross-Account Protection signals | Optional, to end sessions when Google disables an account | After launch |
| Audit erasure exception | `audit_events` is fully append-only until the erasure design exists (OD-18) | With OD-18 |
| Personal debts and loans model | Proposed in `FINANCIAL_RULES.md` §4.10: `debt_payable` and `debt_receivable` ledger accounts, one per debt, so principal is never income or spending. No interest, fees, or forgiveness until approved | Owner, before R3 |
| Recurring Finances schedule | R1 to R3 add about 7 days to Phase A, after A4 (`ROADMAP.md`) | Owner |
| Recurring occurrence horizon | Proposed: occurrences created up to 35 days ahead, when the app opens | Owner, before R1 |

---

## 3. Findings Register

**Status:** **Open** needs action or a decision · **Resolved** handled in the design · **Accepted** a documented limitation · **Verify** can only be confirmed later · **Superseded** replaced by a decision.

### Critical

| ID | Finding | Solution | Status |
|---|---|---|---|
| R-01 | Linked expenses had no defined accounting | OD-1 and OD-16 approved: `trip_clearing`, derived allocation | Resolved; **needs real tests** |
| R-02 | Unclear what counts while a correction awaits confirmation | Immutable versions; old version stays effective | Resolved |
| R-03 | Time-zone changes could rewrite history | Plain effective dates | Resolved |
| R-04 | Transaction currency vs account currency conflict | Always post in account currency | Resolved |
| R-05 | **An unlimited deletion block could operate as an automatic refusal of a legal right, and hold unrelated data hostage to a third party's possible claim** | Three tracks; scoped block; legal review | **Open and unresolved: OD-18. Blocks public production. Not verified by a lawyer** |
| R-07 | The schedule is large, and the revised figure rests on unmeasured savings | Portfolio-first phases. 196–263 focused days as the honest range | **Open: re-estimate after Phase A** |
| R-23 | **Opening sign-up to the public without the safeguards in place** would expose strangers' data | Launch gate with evidence for each Mandatory item | **Open: gate not met. New public accounts stay disabled** |
| R-47 | **Locale-aware amount entry can misread a value by 1,000×** | Canonical API, locale-aware input, visible echo, property tests | Resolved in design; needs tests |
| R-48 | **Arabic PDF rendering is unproven**, and required for the Web MVP | Spike at the start of Phase C; embedded fonts; Chromium fallback | **Verify in Phase C, and at scale in PD4** |
| R-63 | **The demo could be mistaken for a production service,** by visitors or in the own description of it | Banner, no registration, no real data, wording rules, separate acceptance criteria | Resolved in design; **applies to how you describe it too** |
| R-69 | **Google sign-in could enable account takeover** through login CSRF, replay, forged tokens, or email-based matching | Subject ID as identity, state bound to the browser, single use, nonce, PKCE, verified ID tokens, and tests (`SECURITY.md` §2.7) | **Mitigated in development:** tested against a fake Google, and a real sign-in verified end to end in A2.4. Re-check with the production client |

### Important

| ID | Finding | Solution | Status |
|---|---|---|---|
| R-09 | Balances have no category; allocations have no account | Hide with an explanation | Resolved |
| R-10 | Beneficiaries had no recourse against a self-approved expense | Disputes (OD-2) | Resolved; details proposed |
| R-11 | Email is a dependency for notifications in production | Provider through the job queue | Open: OD-6, before the first real email |
| R-12 | WeasyPrint's performance on long tables is unverified on the target hardware | Spike, row caps, isolated worker | **Verify** |
| R-13 | Reference rates differ from the rates people actually pay | Manual rates; provenance always visible | Accepted |
| R-14 | Converting shares individually breaks zero-sum. The model showed this in 65% of cases | Allocate, convert once, reallocate | Resolved |
| R-15 | An editable payer would need two confirmations | Payer is immutable | Resolved |
| R-16 | Unresponsive parties leave items pending | No financial effect; withdrawal; reminders | Accepted |
| R-17 | ECB's terms weren't reviewed | Read at B3 | **Open** |
| R-19 | Confirmations need notifications | Basic in B2; full inbox in C3 | Resolved |
| R-20 | Personal categories could leak onto trip records | System categories only | Resolved |
| R-21 | Paying in another currency can leave 0.01 | `settle_in_full`, with defined tolerance | Resolved |
| R-22 | Personal trip expenses are visible to other members | OD-4 approved; notice before saving | Resolved; former-member scope proposed |
| R-24 | **Applicable law is unknown.** Open registration in three languages widens it. KVKK was read only in partial unofficial form; other countries' laws weren't reviewed | Professional advice; no compliance claims | **Open. Blocks public production** |
| R-25 | Spending looked inflated under the earlier plan | Replaced by OD-1 | Superseded |
| R-26 | `.` decimals can import as text in Turkish spreadsheets | `profile=excel` | Resolved; needs testing |
| R-27 | `transactions` duplicates amount and account | Reports never read it; INV-06 | Resolved |
| R-28 | An erased payer's expenses can't be corrected, since D10 has no override | Documented freeze | Accepted |
| R-29 | Backups retain deleted users' data; a restore could resurrect them | Disclose; `erasure_log` | Resolved in design; **legal check** |
| R-32 | Implied-rate precision on cross-currency transfers | Store amounts as entered | Resolved |
| R-41 | **People may be confused by two figures:** €100 spent and €300 cash out | Clear labels, an explainer, a comparison view, usability tests | **Verify in B5, C8** |
| R-42 | **A manual expense can double-count with an allocated share.** OD-17 raises this: there's no setting to avoid it | Warnings, attach, dismissal, spending filter, labeled components. Detection can't be perfect | Accepted, with mitigation |
| R-43 | **Legal pages in three languages can differ in meaning** | Professional review; a stated controlling language | **Open: legal review** |
| R-46 | **Mass account creation and resource exhaustion** | New-account limits and cap, quotas, kill switch in PD1. Google sign-in raises the cost | Resolved in design; needs testing |
| R-49 | Regional Arabic tags change digits and separators. Tested on ICU 77 | Fixed `ar` tag with `nu-latn`; one `<Money>` component | Resolved; browsers need testing |
| R-50 | **Translation errors can change financial or legal meaning** | Glossary, native review | **Open: reviewers needed by C7** |
| R-52 | **Invisible bidirectional characters can spoof names and amounts** | Reject, strip, isolate | Resolved in design; needs tests |
| R-53 | **Someone else's correction can change the personal spending and budget,** without touching the ledger | A notification explains each change | Accepted |
| R-54 | `ILIKE` and `lower()` mishandle Turkish casing and Arabic variants | A normalized search column; ICU collations | Resolved in design; depends on the server's build |
| R-55 | A 30-day grace leaves almost no margin against a one-month legal deadline | 14 days for self-service | **Open: OD-18** |
| R-58 | No FX gain or loss across currencies | Each currency reported in its own block | Assumption #13 needs the yes |
| R-59 | A trip share in another currency isn't in a local-currency budget | Listed as excluded | Accepted (OD-7) |
| R-60 | Recharts' RTL behavior wasn't confirmed | Spike at C7; table fallback | **Verify** |
| R-61 | A demo could leak real data if it shared production | Fully separate environment | Resolved |
| R-62 | **No inactive-account policy.** Dormant personal data sits indefinitely | Define with legal advice | **Open, before PD2** |
| R-65 | **Deferring Arabic and Turkish until C7 risks an expensive retrofit** | Catalog, locale routes, and logical CSS from A1, with `<Money>` at the first displayed amount. Lint from the start | Resolved in design |
| R-66 | **Pressure to hit an early date could weaken correctness or safety** | Written never-cut list. Deployment is separable from coding | Accepted; needs discipline |
| R-67 | **Demo persona switching could become an authorization bypass** | Same-sandbox only; demo routes absent from production builds, asserted by a test | Resolved in design; needs tests |
| R-70 | **Authentication in A2 delays the first demo by about 4 focused days** | Approved scope, reduced by Google-only sign-in | Accepted |
| R-73 | **Single dependency on Google for sign-in.** An outage stops new sign-ins, and losing a Google account loses access | Sessions survive outages; data export; a manual recovery process before production (`SECURITY.md` §2.4) | **Accepted risk, with an open recovery design** |
| R-74 | **Sessions and CSRF depend on correct origin configuration.** A wrong `WEB_ORIGIN`, or the web app and API on different hosts, would break sign-in or weaken CSRF checks | Startup refuses unsafe settings: https outside development, one shared host, and origin-only values. Tested | Resolved in code; recheck at deployment |
| R-72 | **Test failure output and settings errors could print the database password.** Found and proven at A1 | Validation errors hide input, and test reports redact known passwords. Regression tests added | Resolved, and verified at A1 |

### Optional

| ID | Finding | Suggestion |
|---|---|---|
| R-33 | Greedy suggestions aren't always minimal | Fine as is |
| R-34 | Hash tie-break is harder to explain than user-ID ordering | Keep it |
| R-35 | Full as-of reporting isn't offered, though "What changed?" is | Add it if needed |
| R-36 | Rate-limit counters in PostgreSQL add writes | Review only if measured |
| R-37 | A dark-mode PDF isn't useful | Always light |
| R-38 | A public OpenAPI endpoint helps reviewers but shows the surface | Publish it in the demo only |
| R-39 | Recurring transactions will be requested first | Don't build yet |
| R-40 | Widgets may disappoint if they imply forms | Set expectations |
| R-56 | CJK fonts are large, so PDFs may show boxes for those scripts | Measure, then decide |
| R-57 | Transaction search by amount across locales | Canonical values only |
