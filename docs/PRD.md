# FinPilot — Product Requirements Document

**Status:** Planning document, updated as the project develops · **Date:** 2026-11-05 · **Scope:** Web MVP v1.0, delivered portfolio-first · **Incorporates:** OD-1 to OD-5, OD-7, OD-8, OD-10, OD-16, OD-17, OD-21, OD-24, assumptions #4, #5, and #10, and the language decision

| Document | Answers |
|---|---|
| **PRD.md** (this file) | What are we building, for whom, and how does it behave? |
| `FINANCIAL_RULES.md` | How is every number calculated? |
| `ARCHITECTURE.md` | How is it structured and deployed? |
| `DATABASE.md` | How is data stored and protected? |
| `API_SPEC.md` | What's the client contract? |
| `SECURITY.md` | How is it secured? |
| `ROADMAP.md` | When, in what order, and how is it tested? |
| `RISKS_AND_OPEN_QUESTIONS.md` | What might go wrong, and what do you need to decide? |

**Labels:** **[D1–D12]** approved decisions · **[Confirmed]** a decided OD · **[Rec]** or **[Proposed]** a recommendation made in this plan · **[OD-n]** see `RISKS_AND_OPEN_QUESTIONS.md` for its status.

---

## 1. Vision

FinPilot helps individuals and groups understand, organize, and manage money. It combines **personal finance**, **Trip Mode shared expenses**, and **detailed financial reporting and export**, in a product that should feel like a real commercial fintech app.

**FinPilot is not a bank or a payment processor.** It doesn't connect to bank accounts or move money. Everything is recorded by users, and a "confirmed settlement" means a payment was *reported* as having happened outside FinPilot.

### Goals

| Goal | Measure |
|---|---|
| Fast everyday use | An ordinary expense takes under 10 seconds in Quick Add |
| Trustworthy numbers | All invariants in `FINANCIAL_RULES.md` §17 pass continuously |
| Fair shared expenses | Trip balances always sum to exactly zero, and every number can be traced to its source |
| Reports people can use | PDF and CSV exports match the screen exactly |
| Portfolio credibility | The architecture can be explained and defended, decision by decision |

### Non-goals (MVP)

Bank connections, payments, investments, lending, tax advice, recurring transactions, receipt OCR, shared household budgets, guest participants (D2), multiple payers per expense, native mobile apps (D3).

### Personas

- **Selin, individual tracker:** has a TRY bank account, cash, and some EUR savings. Wants to see where money goes, and stay in budget.
- **Omar, trip organizer:** arranges group trips, pays for many things up front, and wants a fair, clear record of who owes what.
- **Lena, trip participant:** joins a trip, and mostly confirms things and pays what she owes. Won't tolerate complexity.
- **Interviewer or reviewer:** explores a demo account, and looks for evidence of engineering judgment.

---

## 2. Scope

### Three distinct deliveries [Confirmed]

| | **Early Portfolio Demo** (Phases A, B) | **Complete Web MVP v1.0** (Phase C) | **Public Production Launch** (Phase D) |
|---|---|---|---|
| Purpose | Demonstrate real engineering | All approved web features | Real users and real money records |
| Data | **Synthetic only** | Synthetic or staff testing | **Real** |
| Features | A working subset | **Everything below** | The same features |
| Languages | English, with RTL-ready structure | English, Arabic, Turkish, complete | Same |
| Sign-in | **No Google sign-in.** A sandbox session per visitor | Google, **not public** | Google, **open only after the launch gate** |
| Acceptance | §11.1, §11.2 | §11.3 | §11.4 |
| Publicly claims | "A demo with synthetic data" | Nothing public by itself | Only what has evidence |

**The demo isn't a financial data service, and isn't a statement of production readiness.** All Web MVP features below remain in scope. The demo's narrower scope defers nothing beyond Phase C, and OD-5's open registration stays a requirement of the public release.

**One rule applies at every stage: no fake features.** A control that doesn't work isn't shown. Financial calculations are real.

### In the Web MVP

Google sign-in and account management, financial accounts, transactions and transfers, Quick Add, categories, monthly budgets, dashboard and analytics, trips, invitations and roles, trip expenses with approvals and corrections, expense disputes, multi-currency with rate snapshots, settlements with confirmation and reversal, simplified settlement suggestions, optional personal account linking, personal/trip/participant reports, PDF and CSV export, print views, light and dark themes, responsive layouts, **English, Arabic (RTL), and Turkish, complete at release**, and **open public registration**, once the launch requirements in `SECURITY.md` §13 are met.

### Added by this plan because the brief depends on them [Rec]

| Item | Why it's required |
|---|---|
| **Transactional email** | Notifications and notices can't work without it (OD-6). Sign-in doesn't need it |
| **In-app notification inbox** | Confirmations are useless if nobody learns they're waiting |
| **Pending Actions view** | One place for everything needing your decision |
| **Preview endpoints** | Live split results with no calculation in the frontend |
| **Demo account and seed data** | A portfolio reviewer shouldn't need to invent data |
| **Refund transaction type** | Refunds aren't income, and budgets must handle them |
| **Abuse controls, quotas, operator suspension** | Required by open registration (`SECURITY.md` §13) |
| **Privacy notice, terms, privacy-request process** | Required by open registration, in all three languages |
| **Isolated demo environment** | Portfolio visitors must never touch real data |

### Recommended deferrals [Rec]

| Item | Reason |
|---|---|
| Explicit reporting currency (OD-8) | Per-currency is clearer; conversion needs its own methodology |
| Cover image upload (OD-13) | Security surface for a cosmetic feature, more so with open registration; use preset covers. **Awaits your decision, because it narrows a listed feature** |
| MFA, recurring transactions, budget rollover, category merge | Valuable, but post-MVP. MFA is the first post-launch priority |
| FX gain or loss reporting | Needs a defined valuation policy (`FINANCIAL_RULES.md` §11.5) |

---

## 3. Personal Finance Requirements

### 3.1 Accounts and identity

**Anyone with a Google account can join [OD-5 confirmed].** **Sign-in [Approved]: Google only.** There are no passwords, registration forms, reset links, or FinPilot verification emails. The first successful Google sign-in creates the account. Users can sign in and out, edit their profile, set preferred currency, time zone, and language, review and revoke sessions, export data, and request deletion. Security details are in `SECURITY.md` §2.

**Account deletion [OD-3 confirmed preference; safety unresolved]:** normal self-service deletion is blocked while the user has open trip balances or pending items. A legal erasure request is a separate process, and is never refused automatically (`SECURITY.md` §11). OD-18 decides the final shape.

**Acceptance criteria**
- [ ] Continue with Google creates an account on the first sign-in, and signs in to the same account afterwards.
- [ ] A Google account without a verified email creates no account.
- [ ] Revoking a session makes its next request return 401.
- [ ] Sign-in errors don't reveal which check failed.
- [ ] Changing time zone doesn't alter any past budget or report figure.
- [ ] A data export contains only what that user could already see, and stays available throughout any deletion block.
- [ ] New accounts have rate limits, a daily cap, and a working kill switch.
- [ ] Exceeding a quota gives a translated explanation, and never loses data.
- [ ] The deletion screen lists each blocking item and how to resolve it, and says how to make a privacy request.
- [ ] A privacy request is recorded with a due date and reaches a human. No code path refuses it automatically.
- [ ] Signing in with Google creates or opens exactly one FinPilot account, found by Google's subject ID.
- [ ] **An email address never links or signs in to an account.** A new Google account using a reassigned address never gets the previous owner's account.
- [ ] The settings screen says the account depends on the person's Google account.

### 3.2 Financial accounts

Multiple manually tracked accounts of type cash, bank, savings, wallet, or other, with name, currency, opening balance, optional description, and active or archived status. The balance is always derived.

**Acceptance criteria**
- [ ] An account with an opening balance has both the account and its posting, or neither.
- [ ] There's no way to set a balance directly; drift is fixed with an explicit adjustment, with an optional reason (owner decision, 2026-11-06).
- [ ] A transaction that takes a balance negative succeeds, with a clear warning. No fee or interest is ever added (Assumption #5).
- [ ] Archiving requires a zero balance.
- [ ] Currency can't be changed after creation.
- [ ] Other users can't read any account, returning 404.

### 3.3 Transactions and transfers

Income, expense, refund, transfer, and adjustment. Fields, posting, and correction behavior are in `FINANCIAL_RULES.md` §4. History has search, sorting, filtering, and pagination.

**Acceptance criteria**
- [ ] Double-clicking submit creates exactly one transaction.
- [ ] A transfer that fails at any point leaves no partial record.
- [ ] A cross-currency transfer records both real amounts and shows the implied rate.
- [ ] A correction preserves the original entry and adds a reversal and a replacement; history shows what changed and when.
- [ ] Own-account transfers never appear in income or expense totals.
- [ ] Two simultaneous edits: one succeeds and the other gets a clear conflict.

### 3.4 Categories

System income and expense categories as listed in the brief, translated into all three languages, and custom categories. See `FINANCIAL_RULES.md` §5.

**Acceptance criteria**
- [ ] Renaming a custom category updates its display everywhere, with no financial change.
- [ ] An archived category disappears from pickers but remains on old transactions.
- [ ] A category with references can't be deleted.

**As built in A4 (decision recorded 2026-11-06, for review):** instead of shared system rows, each user gets their own copy of the defaults on first use, seeded once: 14 expense categories (Food & Drinks, Groceries, Transportation, Shopping, Housing & Rent, Bills & Utilities, Health, Education, Entertainment, Travel, Personal Care, Subscriptions, Gifts, Other) and 5 income categories (Salary, Freelance & Business, Bonus, Gift Received, Other Income), each with an icon and a color from an app-controlled set. A default keeps its `system_key` and is shown translated until the person renames it; renaming back to the default name restores the translation. Defaults can be renamed, recolored, and archived, like custom ones. No category is ever deleted in A4; archiving is the way to retire one. Settings → Categories manages them.

### 3.5 Budgets

Overall and per-category monthly limits, showing progress, remainder, and overspending. **Each budget uses one currency [OD-7 approved]. Trip spending counts as your allocated share, not what you paid for others [OD-10 approved]:** a €300 bill with a €100 share counts €100. Behavior and category mapping are in `FINANCIAL_RULES.md` §6.

**Acceptance criteria**
- [ ] Changing a limit in June doesn't change May's report.
- [ ] Transfers and adjustments don't count; refunds reduce spending.
- [ ] Your allocated share of approved trip expenses counts, on the expense's date. Cash paid or received for the trip doesn't. Pending and rejected expenses don't (OD-10).
- [ ] A linked expense can't be counted twice.
- [ ] Each budget lists what's included, and what's excluded because of currency (OD-7).
- [ ] A custom-category budget explains why trip shares aren't in it.

### 3.6 Dashboard

Per-currency balances, optional labeled estimate of total, monthly income, **personal spending** (non-trip plus allocated trip shares), **actual cash flow**, spending by category and over time, budget performance, account breakdown, recent transactions, pending actions, and a read-only memo of trip positions (who owes you, what you owe, and anything unrecorded).

**Spending and cash flow are shown as two separate, labeled figures [OD-1, OD-16].** Paying €300 for a shared dinner with a €100 share shows **€100 spent** and **€300 cash out**, with an explanation one tap away. Definitions are in `FINANCIAL_RULES.md` §16.2a.

**Your share of approved trip expenses counts as spending, in every trip [OD-17 approved],** including when nothing is linked. The default is always the same, and the UI explains that an allocated share isn't necessarily money that has left your accounts. On Insights and Reports, you can include, exclude, or view trip spending separately, and compare cash flow with allocated spending. There's no per-trip setting.

**Totals are per currency [OD-8 approved].** No combined reporting currency.

**Acceptance criteria**
- [ ] Every total equals the corresponding report for the same period (INV-14).
- [ ] Reimbursements never appear as income. Outstanding trip amounts are never added to account balances.
- [ ] Different currencies are never added together, except in the labeled estimate that shows rate date and source.
- [ ] The dashboard works with no data, one currency, and several currencies.

### 3.7 Quick Add

Available from every page, by button and keyboard shortcut. Only **amount** is needed if defaults exist.

**Smart defaults:** type = expense; account and category = most recently used; date = today in the user's zone.

**Amount entry is locale-aware and always echoes its interpretation** (`FINANCIAL_RULES.md` M7). `1.234` typed in Turkish visibly becomes `₺1.234,00` before saving.

**Acceptance criteria**
- [ ] A returning user can record an expense in under 10 seconds, using only the keyboard.
- [ ] Amounts typed in each language save as the value shown, including Arabic-Indic digits.
- [ ] Validation errors are shown inline, with no loss of entered data.
- [ ] Retrying after a network failure never creates a duplicate.
- [ ] After saving, the form resets for another entry and shows the new balance.
- [ ] A first-time user with no account is guided to create one.

**As built in A4 (decisions recorded 2026-11-06, for review):**

- **Default accounts are created automatically.** On the first visit, a user with no accounts gets **Cash** and **Bank** in their main currency, at zero, with no journal entries. Cash is the default for new entries. Their names are shown translated until renamed. If no main currency is saved, that's the only question asked. A user who already has accounts keeps them as they are: nothing is added, and the default becomes their earliest active account. Setup is idempotent and safe when two tabs run it at once.
- **The default account** is chosen per user ("Use for new entries" on an account's page). If it's archived, the next active one (Cash, then Bank, then the earliest) takes its place. Quick Add always shows the account's currency code next to the amount; nothing is converted.
- **Category default:** the category used most recently for that kind of entry; with none yet, the person chooses (no silent "Other").
- **Where it lives:** inline at the top of Home, and in a dialog from the top bar's Add button on every page, or the **N** key (on Home, N goes to the inline form). Enter saves; Shift+Enter (and "Save and add another" in the dialog) saves and starts the next one with the same type, category, account and date.
- **Duplicate protection:** each distinct draft has one idempotency key, so a double press or a retry after a network failure records it once.
- **After saving:** a short acknowledgement, then totals, recent entries and balances refresh, and focus returns to the amount.

### 3.8 Recurring Finances [Approved by the owner, 2026-11-06; design proposed]

Track money that's **expected** to arrive or leave on a schedule, and turn each one into a real transaction only when the person confirms it. Three kinds:

| Kind | Examples | Confirming creates |
|---|---|---|
| **Fixed income** | Salary, rent received, regular freelance work, pension | An `income` transaction into the chosen account |
| **Fixed expenses** | Rent, internet, phone, subscriptions, insurance, utilities, family support | An `expense` transaction from the chosen account, in its category |
| **Debts and loans** | Money borrowed, money lent, a personal loan, installments | A repayment, which is **not** income or spending (`FINANCIAL_RULES.md` §4.10, proposed) |

**A rule** has: name, expected amount and currency, account, category (income and expenses), frequency (weekly, monthly, or yearly in the first version), the expected day or date window (for example "the 25th to the 28th of each month"), start date, optional end date, notes, and active or paused status. Every occurrence needs confirmation; nothing is ever recorded automatically.

**Expected vs actual (the core rule).** An expected occurrence **never** changes an account balance, income, spending, or a budget. Only a confirmed occurrence does, through the normal posting workflow, exactly once. Reports may show expected and actual figures side by side, always labelled, never added together.

**Confirming an occurrence.** When one is due, the app asks: "Did you receive your salary?" or "Did you pay this expense?"

| Answer | Effect |
|---|---|
| **Received / Paid** | The person confirms the actual amount, date, and account (prefilled from the rule). One real transaction is created, and the occurrence is marked confirmed. The scheduled amount stays recorded as the expectation, and the actual amount is the transaction's |
| **Not yet** | Nothing changes. It stays pending, and becomes overdue after its window |
| **Skip** | That occurrence only is closed, with no transaction. The rule continues |

Past occurrences (confirmed, skipped, overdue) stay reviewable.

**Statuses shown:** Expected (window not reached), Pending (window open, awaiting an answer), Overdue (window passed, no answer), Confirmed, Skipped. Income, expenses, and debts are visually distinct.

**Reminders** are in-app only in the first version: computed when the app opens, from the occurrences themselves, so there's exactly one per occurrence. No email, SMS, push, or background worker.

**A dedicated Recurring page** shows: pending confirmations first, then upcoming income and expenses, active rules, debts and installments, and history. Compact cards and tables, quick confirm actions, all three languages, RTL, light and dark.

**Acceptance criteria**
- [ ] No expected occurrence ever changes a balance, income, spending, or budget figure.
- [ ] Confirming one occurrence twice, from two tabs or by retrying, creates exactly one transaction.
- [ ] Skipping an occurrence creates nothing and leaves the rule active.
- [ ] A window of the 29th to the 31st lands on the last day of shorter months, and on 28 or 29 February correctly.
- [ ] Editing a rule never changes a confirmed or skipped occurrence.
- [ ] Pausing stops new occurrences, and resuming doesn't invent missed ones as due.
- [ ] Amounts in different currencies are never added together.
- [ ] A debt repayment never counts as spending, and money received back never counts as income.

**As built in M5 (2026-11-06, for review):** all three kinds, plus installment plans (an expense rule with a payment count) and debt installments. A dedicated Recurring page lists answers waiting, then what's coming, then history, with the rules and debts beside them. Home shows the three nearest items with Received/Paid and Not yet. A rule may expect another currency; confirming converts as in Quick Add. Debts follow `FINANCIAL_RULES.md` §4.10.

### 3.9 Multi-currency spending and Amount Equivalent [Built in M5]

- **Quick Add in any currency.** The amount can be entered in another currency than the account's. Before saving, the person sees what they entered, the estimated amount for the account, the reference rate, its source and date; they can accept it, type the amount actually charged, or type their own rate. The account is always changed in its own currency; the original amount is kept and shown on the transaction. Rules: `FINANCIAL_RULES.md` §7.5.
- **Amount Equivalent.** From account pages (a compact "Roughly …" line) and from Home's Go to list: roughly what an amount is worth in favourite currencies, grams or troy ounces of fine gold, and Bitcoin, with each quote's source and time. Display only (§7a).

**Acceptance criteria**
- [x] An expense of $22 from a euro account changes the account by the euro amount only; the dollar amount appears on the transaction, never in totals.
- [x] With no rate available, nothing is invented: the person enters the amount charged or a rate.
- [x] An old rate is labelled with its date and needs acknowledgement.
- [x] A recorded conversion never changes when market rates move.
- [x] Equivalents never change a balance, and an unavailable quote shows no number.

---

## 4. Trip Mode Requirements

### 4.1 Trip lifecycle

| Status | New expenses, corrections | Settlements | Notes |
|---|---|---|---|
| **Active** | Yes | Yes | |
| **Completed** | No | **Yes** | People often settle after a trip ends. Pending expense versions must be resolved first |
| **Archived** | No | No | Read-only; hidden from default lists |

- **Complete:** owner or admin. Blocked while expense versions are pending; an admin can withdraw proposals, since those have no financial effect.
- **Reopen (completed → active):** owner or admin; audited. To correct an expense on a completed trip, reopen it first (D11 still holds).
- **Archive:** owner only. Warns if balances remain, and needs explicit acknowledgment to continue. This matters when a deleted member's balance can never be cleared.
- **Unarchive:** owner only, returning to completed.
- **Base currency:** locked once any expense has been created.

**Acceptance criteria**
- [ ] The UI and API both block expense changes when not active, with a message explaining what to do.
- [ ] Settlements still work on completed trips.
- [ ] Every status change is in the activity feed.

### 4.2 Participants [D2]

All participants are FinPilot users, signed in with Google. Invitations work by a single-use secure link. Roles and permissions are in `SECURITY.md` §4.

| Event | Rule |
|---|---|
| **Join late** | Not added to past expenses automatically. New expenses default to all active members. A past expense can be corrected to include them |
| **Leave** | Allowed only when their net is zero and no pending items involve them |
| **Removed** | Admin or owner. History remains; any outstanding balance stays visible. They become a former member, who can still settle and confirm |
| **What a former member can see [Proposed]** | Only expenses and settlements they're part of, and their own balance. **No longer other members' personal trip expenses.** See `SECURITY.md` §4.4 |
| **Owner leaves** | Must transfer ownership first |
| **Rejoin** | Reactivates the same membership and history |
| **Account deleted** | See `FINANCIAL_RULES.md` §15. Name becomes "Deleted user" |

**Beneficiary rules:** new beneficiaries must be active. A correction can keep existing beneficiaries who've since left, but can't add inactive members.

**Payer rules:** a new expense's payer must be an active member. A former member can still confirm their pending items.

**Frozen expenses:** once a payer's account is erased, nobody can confirm a change to their expenses, so those expenses become permanently fixed. This is an accepted consequence of D10, which allows no override.

**Acceptance criteria**
- [ ] Removing a member changes no one's balance.
- [ ] A removed member with debt still appears in balances and suggestions.
- [ ] A used, expired, or revoked link fails with a clear explanation.
- [ ] Members can't see one another's email addresses, or any other profile data beyond display name and role.
- [ ] A former member gets 404 for expenses outside their scope.

### 4.3 Expenses [D7, D10, D11]

Each expense has a payer, amount, currency, description, category, date, beneficiaries, split method, shares, rate snapshot, status, optional notes, and full history. Every rule is in `FINANCIAL_RULES.md` §8 and §12.

**In plain terms:**

- Anyone can record an expense and name anyone as the payer.
- **If that's you, it's approved right away. If it's someone else, they have to confirm it.**
- Only approved expenses affect balances.
- Corrections work the same way. Until a correction is approved, the old version keeps counting.
- Changing the payer means cancelling and creating a new expense.
- **If you've been assigned part of an expense and think it's wrong, you can dispute it [OD-2 confirmed].** A dispute flags it and notifies people. It doesn't change any balance, and settlements stay available. Rules are in `FINANCIAL_RULES.md` §12.8.

**Visibility [OD-4 confirmed]:** every active member can see every trip expense, including ones marked personal. The form says so before saving, whenever the split is payer-only (`SECURITY.md` §4.5). Personal accounts, balances, unrelated transactions, link data, and profile data stay private.

**Acceptance criteria**
- [ ] A dispute changes no balance, and appears in all reports as a marker.
- [ ] A payer, or proposer, can't dismiss a dispute on their own expense.
- [ ] Approving a correction closes open disputes on the old version, and notifies the people who raised them.
- [ ] The visibility notice appears before saving a personal trip expense.
- [ ] A pending or rejected expense changes no balance anywhere.
- [ ] Only the payer can approve; admins and owners get 403 unless they are the payer.
- [ ] Approving an outdated version fails, because approval targets a specific version.
- [ ] A rejected correction leaves the previous version counting.
- [ ] Full version history is visible, with who, when, and what.
- [ ] Shares sum exactly to the total, in both original and base currency.
- [ ] A pending item never looks like a confirmed one: it has a text label and is not distinguished only by color.

### 4.4 Multi-currency [D4, D5]

Each record preserves original amount and currency, base amount, rate, source, and rate date. Rules are in `FINANCIAL_RULES.md` §7.

**Acceptance criteria**
- [ ] A later change in market rates doesn't alter any existing record.
- [ ] Every converted amount shows its rate, source, and date.
- [ ] Manual rates show a visible badge to all members.
- [ ] When the provider is down, a user can still complete the expense with a manual rate or an acknowledged stale one.
- [ ] Currencies with 0, 2, and 3 decimal places all compute correctly.

### 4.5 Settlements [D6, D11, D12]

See `FINANCIAL_RULES.md` §9, §10, §13.

**Acceptance criteria**
- [ ] A pending payment doesn't change any confirmed balance.
- [ ] Only the recipient can confirm a payment.
- [ ] Partial payments reduce the balance by exactly that amount.
- [ ] A later expense correction leaves confirmed settlements unchanged, and recalculates what remains.
- [ ] An overpayment warns but is allowed; the balance then flips.
- [ ] A reversal needs the other party's confirmation, and the original stays visible, marked as reversed.
- [ ] Suggestions are deterministic, and described as "suggested", never "optimal".
- [ ] Pending settlements appear beside suggestions, to prevent paying twice.

### 4.6 Trip dashboard

Total spending; shared and personal; category breakdown; paid and allocated per member; net balances; pending approvals; pending and confirmed settlements; outstanding balances; recent activity.

**Acceptance criteria**
- [ ] Member nets sum to exactly zero, and paid equals allocated equals total spending.
- [ ] Each number links to the records behind it.
- [ ] Items waiting on the current user are prominently first.

### 4.7 Personal account linking [D1, D8, OD-1 confirmed]

Optional. Rules are in `FINANCIAL_RULES.md` §11.

**The approved behavior.** Alex pays €300 for a shared expense with a €100 share:

| | After paying | After €200 confirmed and linked |
|---|---|---|
| Bank account | −€300 | −€100 |
| Actual cash out, net | €300 | €100 |
| Personal spending | €100 | €100 |
| Outstanding reimbursement | €200 | €0 |
| Income | No change | No change |

If the €200 is confirmed but not linked, the reimbursement still falls to €0, the bank stays at −€300, and the report says that €200 received isn't recorded in his accounts.

**Your share always counts as personal spending [OD-17 approved].** If you also recorded the same expense by hand, FinPilot warns you and asks. It never merges anything by itself (`FINANCIAL_RULES.md` §11.7).

**Acceptance criteria**
- [ ] A trip expense is fully valid with no link.
- [ ] Only the payer can link an expense, and only to their own account.
- [ ] Linking is atomic: failure leaves no ledger entry and no link.
- [ ] Attaching an existing manual transaction moves it out of ordinary spending, with a clear notice, so it can't count twice.
- [ ] A trip event never changes a personal ledger; the owner sees a review prompt instead.
- [ ] No other member can tell whether a link exists (tested in API responses, activity feed, and all report formats).
- [ ] With everything linked, cash put into the trip equals share plus balance owed, exactly, in base currency, after any sequence of corrections and settlements.
- [ ] A reimbursement never counts as income, whether or not the original expense was linked.
- [ ] A likely manual duplicate raises a warning. The user can attach, void, or dismiss it. Dismissal is remembered, and no action happens without confirmation.
- [ ] Attaching leaves the account balance unchanged, and the original stays in history.
- [ ] Another member's correction never creates or changes a transaction in your account.

---

## 5. Reporting Requirements

Calculation semantics are in `FINANCIAL_RULES.md` §16. Architecture is in `ARCHITECTURE.md` §6.

### 5.1 Personal reports

**Filters, combinable:** custom range, month, year, account, category, type, currency, and trip allocations (include, exclude, or only; include is the default).

**Report dates:** rows distinguish the effective financial date, when it was recorded, and when it was corrected. A correction changes the original period [Assumption #4 approved], so a new report can differ from an earlier one. "What changed since this report?" explains the difference from history (`FINANCIAL_RULES.md` §16.9). **An approved expense counts on its original date,** with its approval time shown separately [Assumption #10 approved].

**Contents:** per-currency opening and closing balances, income, non-trip spending, trip cash out and in, **actual cash flow**, internal transfers and conversions, reconciliation row, **personal spending** (non-trip plus allocated shares), spending by category, trip positions (allocated, outstanding, unrecorded), transaction count, account breakdown, and detail table. Which figure appears where is defined in `FINANCIAL_RULES.md` §16.7.

**Detail table columns:** date and time, description, category, account, type, original amount and currency, posted (converted) amount when relevant, status. Filtering, sorting, pagination, search, and export.

**Acceptance criteria**
- [ ] The identity `Closing = Opening + movements` holds and is displayed.
- [ ] With a category or type filter active, balances are hidden, with an explanation.
- [ ] Own-account transfers never count as income or expenses.
- [ ] Reimbursements never count as income, and trip cash never counts as spending.
- [ ] With an account filter, allocation is hidden, with an explanation.
- [ ] Multi-currency reports show separate per-currency blocks and no combined total.
- [ ] Figures are identical in all three languages (INV-24).
- [ ] The trip-allocation filter changes spending figures only, and always adds up.
- [ ] An expense appears consistently in cash flow, spending, budget, trip report, and settlements (`FINANCIAL_RULES.md` §20.2).
- [ ] A later correction to an earlier period is listed by "What changed since this report?", and accounts for the difference.
- [ ] Filters are preserved in the URL, so a report can be bookmarked.
- [ ] The frontend performs no financial calculation (enforced by code review and absence of arithmetic on amount strings).

### 5.2 Trip reports

**Contents:** trip name, dates, base currency, participants, total spending, shared and personal, by category, detailed expense records, payer, beneficiaries, shares, paid and allocated per member, net balances, suggested settlements, pending settlements, confirmed settlements, remaining balances.

**Acceptance criteria**
- [ ] Pending and rejected items appear only in clearly labeled separate sections, and are never in totals.
- [ ] Disputed expenses are marked, stay in totals, and also appear in an open-disputes section.
- [ ] Trip reports contain no personal cash data for any member.
- [ ] Corrected expenses show current values and a corrected marker.
- [ ] Rows show original amount, rate, rate source and date, and base amount.
- [ ] A settlement confirmed before a later correction appears correctly, and the remaining balance reconciles.

### 5.3 Participant reports

Expenses they paid, their shares, confirmed settlements paid and received, outstanding balance, relevant history.

**Acceptance criteria**
- [ ] Contains no private account information from any member, including the participant's own links, unless that participant is viewing their own report and asks to include them. **Default: excluded.**
- [ ] Former members can get only their own.

### 5.4 PDF export

A4 portrait by default; landscape for wide tables. FinPilot branding, report title, applied filters, date range, generation timestamp and time zone, currency details, summaries, detail tables, totals and subtotals, page numbers, repeated table headers, consistent money formatting, explicit pending and confirmed statuses. No screenshots.

**Acceptance criteria**
- [ ] Automated test: a 5,000-row PDF (or whatever limit the spike sets) has correct page count and page numbers, and repeated headers on sampled pages.
- [ ] PDF text is extractable and selectable, proving it isn't an image.
- [ ] Totals in the PDF equal the API's (extracted and compared).
- [ ] Reports generate in English, Arabic, and Turkish, with embedded fonts, and a person confirms Arabic shaping and RTL layout.
- [ ] Amounts and currency codes are never reversed or reordered in Arabic reports.
- [ ] Headers, labels, and statuses are fully translated.
- [ ] A row or summary block never splits awkwardly across pages.
- [ ] The user sees progress, a ready state, and a clear failure with a retry.

### 5.5 Print

**Acceptance criteria**
- [ ] Navigation and controls are hidden; A4 layout; headers repeat where the browser supports it.
- [ ] Metadata matches the PDF: title, filters, generation time, zone.
- [ ] Above 2,000 rows, the UI recommends PDF rather than silently truncating.
- [ ] Verified manually in current Chrome, Firefox, and Safari, in LTR and RTL.

### 5.6 CSV export

Personal transactions; **own trip allocations**; trip expenses; participant balances; settlements. Format in `API_SPEC.md` §13.4. Headers follow the export's language; numbers and dates don't change by language.

**Acceptance criteria**
- [ ] Respects the active filters exactly.
- [ ] A row-by-row comparison with the API shows a match.
- [ ] Round-trip parsing succeeds with commas, quotes, newlines, Arabic, and Turkish.
- [ ] Formula injection is neutralized in all text columns.
- [ ] Unauthorized requests get 404.

**Turkish spreadsheets [Rec]:** a `.` decimal may import as text in comma-decimal spreadsheet locales. Since Turkish is now a launch language, an "Excel-friendly" option with a semicolon and comma decimal is included, rather than deferred (R-26).

---

## 6. Navigation and Information Architecture

**Top level is deliberately small.** Trip Reports and Settlements belong inside a trip, since that's where people look for them.

```
Public
├── Landing
├── Sign in (Continue with Google)
└── Invitation landing (/invite)

App
├── Home            Dashboard + Pending Actions
├── Accounts        List → Account detail
├── Transactions    History, filters → Detail
├── Budgets         Monthly overview
├── Recurring       Fixed income, fixed expenses, debts and installments (§3.8)
├── Insights        Analytics
├── Reports         Personal reports → Export
├── Trips           List → Trip workspace
│   └── Trip
│       ├── Overview     Dashboard
│       ├── Expenses     List → Detail + History
│       ├── Balances     Nets, suggestions, settlements
│       ├── Reports      Trip + Participant
│       └── Members      Invitations and roles
└── Settings        Profile, Security, Sessions, Preferences, Data

Always available: Quick Add · Notifications
```

### Mapping to the 14 logical pages

| Page in brief | Location |
|---|---|
| Landing, Authentication | Public |
| Personal Dashboard | Home |
| Financial Accounts, Transactions, Budgets | Same names |
| Analytics | Insights |
| Financial Reports | Reports |
| Trips, Trip Details | Trips, Trip → Overview |
| Trip Expenses, Trip Reports | Inside the trip |
| Settlements | Trip → Balances |
| Settings | Settings |

**Layout:** sidebar on the start side (left in English and Turkish, right in Arabic) on desktop, collapsible on tablets, bottom tab bar with central Quick Add on phones. Every route carries its language: `/en/…`, `/ar/…`, `/tr/…`.

**Public pages added by OD-5:** Privacy notice, Terms, Privacy request form, Security contact. **Settings adds:** Language, Privacy and deletion. **In the demo,** navigation shows only what exists at that phase.

---

## 7. Design Direction

**Calm, precise, and trustworthy, with the character of a well-made personal tool rather than an admin panel.**

| Principle | In practice |
|---|---|
| Numbers are the heroes | Large, tabular-figure numerals; currency code always visible when more than one is present |
| Status is explicit | Pending, approved, and confirmed use text and icon, not only color |
| One primary action per view | "Confirm payment" is obvious |
| Explain the money | Each computed figure can open a "how was this calculated?" view |
| Honest empty states | Teach the next step |
| Generous density control | Roomy dashboards, compact tables |

**Avoid:** rows of identical stat cards, sidebar-plus-cards templates, gratuitous gradients, and decorative charts.

**Visualizations:** horizontal bars for category comparison, line or area for trends, stacked bar for budget progress, diverging bars for trip nets (credit one way, debt the other). Use pie charts sparingly. Tables are available as an accessible alternative to every chart.

**Themes:** light and dark from design tokens. Positive and negative semantic colors are checked for contrast in both.

**Accessibility:** WCAG 2.2 AA target; full keyboard use; visible focus; form errors linked to fields; charts with text alternatives; respects reduced-motion. Verified with automated axe checks and manual keyboard passes, with no claim of formal conformance.

**Responsive:** every flow works from 360 px wide. Wide tables become stacked rows on phones.

**Languages [Confirmed]:** English (`en`), Arabic (`ar`, right-to-left), and Turkish (`tr`). All three must be complete at the initial production release, with no hardcoded text. The architecture is in `ARCHITECTURE.md` §14.

| Requirement | Acceptance criterion |
|---|---|
| Complete translations | CI fails on any missing key, error code, or notification, in any language |
| Quality | Native-speaker review of all UI text, and a shared financial glossary |
| Language choice | Selector in onboarding, Settings, and public header; remembered per account |
| RTL layout | Every flow in §8 works in Arabic: navigation, forms, tables, charts, dialogs |
| Money and identifiers | Amounts, currency codes, and IDs are never reversed. A single component renders them |
| Formatting | Numbers and dates follow the language. Calendar is always Gregorian. Time zone is independent of language |
| Validation and errors | All translated, including server errors and quota messages |
| Notifications and email | Translated, in the reader's language |
| Screen readers | Labels translated and pronounced in the right language |
| User text | Any script displays correctly on the web, each in its own isolated run. PDF coverage limits are documented (`ARCHITECTURE.md` §14.11) |

---

## 8. User Flows and Wireframes

Wireframes show structure and content only; visual design comes later.

### 8.1 Onboarding

```
Continue with Google → Accept terms → Welcome
  → Set currency and time zone (prefilled from browser)
  → Add first account (name, type, currency, optional balance)
  → Dashboard with first-run checklist
```

```
┌─ Welcome to FinPilot ───────────────────────────┐
│  Step 2 of 2: Add your first account            │
│                                                 │
│  What's it called?  [ Cash wallet            ]  │
│  Type   (•) Cash ( ) Bank ( ) Savings ( ) Other │
│  Currency           [ TRY ▾ ]                   │
│  Current balance    [ 1,500.00 ]  (optional)    │
│                                                 │
│  Skip for now                [ Add account ]    │
└─────────────────────────────────────────────────┘
```

**Invited users** continue with Google, and land directly on the invitation. Personal account setup is offered afterward, and it's optional.

### 8.2 Adding a financial account

```
Accounts → Add account → Form → Saved → Account detail
```

The currency field has a hint: *"Can't be changed later."*

### 8.3 Quick expense entry

```
Press "+" or Q → Sheet opens, focus on amount
  → Type amount → Enter → Saved → Toast with balance + Undo
```

```
┌─ Quick Add ─────────────────────────── esc ─┐
│  [Expense] Income  Transfer                 │
│                                             │
│        ₺ [ 450.00          ]                │
│                                             │
│  Account   [● Cash wallet ▾]                │
│  Category  🍽 Food  🚌 Transport  🛒 Shop …  │
│  Today ▾        Add a note…                 │
│                                             │
│  ✓ Save another          [ Save  ⏎ ]        │
└─────────────────────────────────────────────┘
  Saved · Cash wallet now ₺1,050.00 · Undo
```

**Undo** is a void: a real reversal, shown as such in history.

### 8.4 Transferring money

```
Quick Add → Transfer → From, To → Amount(s) → Save
```

```
┌─ Transfer ──────────────────────────────────┐
│  From [ Ziraat TRY ▾ ]  ₺12,450.75 available│
│  To   [ EUR Savings ▾ ]                     │
│                                             │
│  Amount sent      ₺ [ 3,500.00 ]            │
│  Amount received  € [   100.00 ]            │
│  ℹ Rate you got: 1 EUR = 35.00 TRY          │
│    Today's ECB rate: 35.21 — Use this       │
│                                             │
│  Not counted as income or spending.         │
│                            [ Transfer ]     │
└─────────────────────────────────────────────┘
```

Both amounts appear only when the currencies differ.

### 8.5 Creating a budget

```
Budgets → Set a budget → Overall or category → Currency, limit → Save
```

```
┌─ November 2026 ─────────────  ‹  ›  [+ Budget]┐
│  Overall (TRY)      ▓▓▓▓▓▓▓░░░  ₺14,200 / 20,000│
│                     ₺5,800 left · 25 days in  │
│  Food and drinks    ▓▓▓▓▓▓▓▓▓░  ₺4,500 / 5,000 │
│  Transport          ▓▓▓▓▓▓▓▓▓▓▓ ₺2,300 / 2,000 │
│                     Over by ₺300               │
│  ℹ €45 of Food spending isn't in this budget. │
└───────────────────────────────────────────────┘
```

### 8.6 Creating a trip

```
Trips → New trip → Name, destination, dates, base currency → Create → Invite
```

The base currency field carries a hint: *"All balances will be shown in this currency. Locked after the first expense."*

### 8.7 Inviting participants

```
Trip → Members → Invite → Copy link or email
  Recipient: opens link → continues with Google → sees trip name and inviter → Accept or Decline
```

```
┌─ You're invited ──────────────────────────┐
│  Omar invited you to                      │
│  🌍 Rome 2026   ·   12–18 Nov             │
│                                           │
│  Everyone on this trip can see all trip   │
│  expenses, including ones marked          │
│  personal. Your bank accounts and         │
│  personal transactions stay private.      │
│                                           │
│  Your share of approved expenses will     │
│  appear in your personal spending.        │
│                                           │
│  [ Decline ]              [ Join trip ]   │
└───────────────────────────────────────────┘
```

### 8.8 Recording a shared expense

```
Trip → Add expense → Amount, currency → Who paid → Split → Save
  Payer is me → Approved → Offer: "Record in my personal accounts?"
  Payer is someone else → Pending → They're notified
```

```
┌─ Add expense · Rome 2026 ───────────────────────┐
│  What for?  [ Dinner at Roscioli       ]        │
│  Amount     [ 100.00 ] [ USD ▾ ]                │
│             ≈ €89.49 · 1 USD = 0.8949 EUR       │
│             ECB, 30 Oct · Use a different rate  │
│  Who paid?  [ Me ▾ ]      Date [ 5 Nov ▾ ]      │
│  Category   [ Food and drinks ▾ ]               │
│                                                 │
│  Split  (•)Equal ( )Amounts ( )Percent          │
│   ☑ Omar     $33.34   €29.83                    │
│   ☑ Lena     $33.33   €29.83                    │
│   ☑ Bilal    $33.33   €29.83                    │
│   ☐ Sara                                        │
│  Total matches ✓                                │
│                                                 │
│  ☐ Also record in my personal accounts          │
│                               [ Save expense ]  │
└─────────────────────────────────────────────────┘
```

**When the split is payer-only,** the form shows the OD-4 notice before saving: *"Personal doesn't mean private. Everyone on this trip can see this expense."*

The split lines come from the preview endpoint, not frontend calculation. Which member gets the extra cent in USD depends on the hash tie-break. Note that the base amounts are all equal here even though one USD share is larger: €89.49 at these weights allocates to €29.83 each, and the total still matches exactly.

### 8.9 Confirming another person's expense

```
Notification or Pending Actions → Review → Confirm or Reject
```

```
┌─ Lena says you paid for this ───────────────┐
│  Taxi from the airport                      │
│  ₺2,400.00  ≈ €43.01                        │
│  Date: 12 Nov                               │
│                                             │
│  If you confirm:                            │
│   • You're credited as having paid €43.01   │
│   • Split: You €14.34, Lena €14.34,         │
│     Bilal €14.33                            │
│                                             │
│  ☐ Also record in my personal accounts      │
│                                             │
│  [ That's not right ]     [ Yes, I paid ]   │
└─────────────────────────────────────────────┘
```

**"That's not right"** asks for an optional reason, then rejects. Corrections show a before-and-after comparison.

### 8.10 Viewing settlement recommendations

```
┌─ Balances · Rome 2026 ──────────── EUR ─────┐
│  Omar   +€70.00 ████████████│               │
│  Bilal  −€30.00         ████│               │
│  Lena   −€40.00        █████│               │
│                                             │
│  Suggested payments                         │
│  Lena → Omar   €40.00     [ I paid this ]   │
│  Bilal → Omar  €30.00                       │
│  ℹ Based on confirmed items. These may not  │
│    be the fewest possible payments.         │
│                                             │
│  Waiting for confirmation                   │
│  ⏳ Lena → Omar  €20.00  · 2 Nov            │
│                                             │
│  How was this calculated?                   │
└─────────────────────────────────────────────┘
```

"I paid this" appears only on suggestions where the current user is the payer.

### 8.11 Recording and confirming payments

```
Payer: "I paid this" → Amount (prefilled, editable) → Date, note → Submit → Pending
Recipient: Notification → Confirm or Reject → Balances update
```

```
┌─ Did you receive this? ─────────────────────┐
│  Lena says she paid you                     │
│  €40.00 · 5 Nov · "Bank transfer"           │
│                                             │
│  Confirming will reduce what Lena owes.     │
│  ☐ Record in my personal accounts           │
│                                             │
│  [ I didn't receive it ]    [ Confirm ]     │
└─────────────────────────────────────────────┘
```

### 8.12 Generating financial reports

```
Reports → Pick period → Add filters → View summary → Explore table
```

```
┌─ Reports ─────────────────── [PDF] [CSV] [Print]┐
│  Period [ October 2026 ▾ ]  Accounts [ All ▾ ]  │
│  Category [ All ▾ ]  Type [ All ▾ ]  Currency…  │
│                                                 │
│  ── TRY ───────────────────────────────────     │
│  Opening balance                  ₺10,000.00    │
│  + Income                         ₺30,000.00    │
│  − Expenses (after refunds)       ₺18,000.00    │
│  − Converted to other currencies   ₺3,500.00    │
│  = Closing balance                ₺18,500.00    │
│  Actual cash flow: ₺12,000.00 · 58 transactions │
│                                                 │
│  ── EUR ───────────────────────────────────     │
│  Opening balance                   €1,000.00    │
│  + Income                            €500.00    │
│  − Expenses (after refunds)          €120.00    │
│  − Trip cash out                     €300.00    │
│  + Trip cash in (reimbursements)     €200.00    │
│  = Closing balance                 €1,280.00    │
│  Actual cash flow: +€280.00                     │
│                                                 │
│  What things cost you (EUR)                     │
│  Non-trip spending                   €120.00    │
│  Your share of trips                 €100.00    │
│  Personal spending                   €220.00    │
│                                                 │
│  Trip positions (not part of your balance)      │
│  Rome 2026: share €100 · owed to you €0         │
│                                                 │
│  Spending by category   ▓▓▓▓▓ Food ₺6,200 …     │
│                                                 │
│  Date    Description  Category  Account  Amount │
│  ───────────────────────────────────────────    │
│  31 Oct  Groceries    Food      Cash     −₺450  │
│                                  Load more      │
└─────────────────────────────────────────────────┘
```

### 8.13 Exporting PDF and CSV

```
CSV: Click → Download starts right away, with current filters
PDF: Click → Choose orientation → "Preparing…" → Ready → Download
     Leaving the page is fine: a notification appears when ready
```

```
┌─ Export PDF ──────────────────────┐
│  October 2026 · All accounts      │
│  About 58 transactions            │
│  Layout (•) Portrait ( ) Landscape│
│               [ Generate PDF ]    │
├───────────────────────────────────┤
│  ⏳ Preparing your report…         │
├───────────────────────────────────┤
│  ✓ Ready · 3 pages · 142 KB        │
│    [ Download ]                   │
│    Available for 24 hours         │
└───────────────────────────────────┘
```

**Too large:** "This report has 12,400 rows; PDF supports 5,000. Narrow the dates, or export CSV."

### 8.14 Printing reports

```
Click Print → Print view loads the full report → Browser print dialog
```

The print layout shows: logo and report title, filters and period, "Generated 5 Nov 2026, 14:30 Europe/Istanbul", summaries, full table, with no navigation or controls.

### 8.15 Disputing an expense [OD-2]

```
Expense detail → "Something's wrong with this" → Reason → Submit
  → Owner, admins, payer, and proposer are notified → Marked "Disputed" for everyone
```

```
┌─ Dispute this expense ──────────────────────┐
│  Dinner · €300.00 · paid by Omar            │
│  Your share: €100.00                        │
│                                             │
│  What's wrong?                              │
│  [ I wasn't at this dinner               ]  │
│                                             │
│  All trip members can see this. It doesn't  │
│  change any balance. Only a correction      │
│  confirmed by Omar can do that.             │
│                                             │
│  [ Cancel ]                 [ Raise dispute ]│
└─────────────────────────────────────────────┘
```

**Resolver's view:** Propose correction, or Resolve (upheld or dismissed, with a required note). If the viewer is the payer or proposer, Resolve isn't offered, and the screen says why.

### 8.16 Deleting an account [OD-3, depends on OD-18]

```
Settings → Privacy and deletion → Check eligibility
  Nothing open → Confirm (recent sign-in) → Grace period → Erased
  Open items  → See each blocker, with a link to resolve it
```

```
┌─ Delete your account ───────────────────────┐
│  You can't delete your account yet.         │
│                                             │
│  Rome 2026                                  │
│   You owe Omar €40.00          [ Settle ]   │
│  Lisbon 2026                                │
│   1 expense awaits your decision [ Review ] │
│                                             │
│  Available anytime                          │
│   [ Export my data ]                        │
│   [ Erase my personal finance data ]  *     │
│   [ Make a privacy request ]                │
│                                             │
│  * Removes your accounts and transactions.  │
│    Your trips aren't affected.              │
└─────────────────────────────────────────────┘
```

**Items marked with * are proposed under OD-18**, and appear only if it's approved.

### 8.17 Language and right-to-left [Confirmed]

**The Quick Add sheet from §8.3, in Arabic.** Layout mirrors, while the amount, its currency code, and digits stay in reading order.

```
┌─ esc ──────────────────────── إضافة سريعة ─┐
│                     تحويل   دخل   [مصروف]  │
│                                            │
│                [ 450.00          ] TRY     │
│                                            │
│                [▾ محفظة نقدية ●]   الحساب   │
│         … تسوق 🛒   نقل 🚌   طعام 🍽   الفئة │
│                 أضف ملاحظة…        ▾ اليوم  │
│                                            │
│       [ ⏎  حفظ ]           حفظ وإضافة أخرى ✓ │
└────────────────────────────────────────────┘
```

Arabic strings in this wireframe are placeholders for layout only. The final wording comes from native-speaker review.

### 8.18 Personal spending vs cash flow explainer [OD-1]

Opened from "How is this calculated?" on the dashboard.

```
┌─ Why are these two numbers different? ──────┐
│  Spent this month        €100.00            │
│  Cash out this month     €300.00            │
│                                             │
│  You paid €300 for a Rome dinner.           │
│  Your share was €100.                       │
│  Others still owe you €200.                 │
│                                             │
│  When they pay you, your account goes up,   │
│  and what you spent stays €100.             │
│  It isn't income.                           │
└─────────────────────────────────────────────┘
```

### 8.19 Possible duplicate review

Shown from a notification, or the "Possible duplicates" card.

```
┌─ Is this the same expense? ─────────────────┐
│  Your entry            Trip expense         │
│  Dinner                Roscioli, Rome 2026  │
│  €300.00 · 12 Jan      €300.00 · 12 Jan     │
│  Food · Bank           You paid · share €100│
│                                             │
│  If they're the same, your €300 moves out   │
│  of Food spending and your €100 share       │
│  counts instead. Your balance won't change. │
│                                             │
│  [ They're different ]   [ Same: attach ]   │
└─────────────────────────────────────────────┘
```

### 8.20 Portfolio demo entry

```
┌─ FinPilot demo ─────────────────────────────┐
│  A working demo with synthetic data.        │
│  Your copy is private and resets nightly.   │
│  Please don't enter real information.       │
│                               [ Try demo ]  │
└─────────────────────────────────────────────┘
  In the app: [ Demo · synthetic data · resets 03:00 UTC ]
  Phase B adds: Viewing as: Omar ▾  (switch persona)
```

There's no sign-in link in the demo, rather than a disabled one.

### 8.21 Signing in [Approved: Google only]

```
┌─ Sign in to FinPilot ───────────────────────┐
│                                             │
│      [ G  Continue with Google ]            │
│                                             │
│  New here? The same button creates your     │
│  account.                                   │
└─────────────────────────────────────────────┘
```

**Errors are short and translated:** cancelled, try again; Google is unavailable right now; this Google account needs a verified email; this account is unavailable. **None of them says which security check failed.**

**In the demo,** this screen doesn't exist.

---

## 9. Non-Functional Requirements

| Area | Target (to verify; not yet measured) |
|---|---|
| Interactive API | p95 under 200 ms on the deployment host, at expected load |
| Report summary | p95 under 300 ms at 100k lines |
| Quick Add | Keyboard-only, under 10 seconds |
| Web performance | Largest Contentful Paint under 2.5 s on a typical connection, for the dashboard |
| PDF | A 100-row report ready in under 10 s (provisional until the spike) |
| Availability | Best effort for a single self-hosted server; **no SLA is offered** |
| Recovery | RPO 24 h, RTO 4 h; verified by a restore test |
| Browsers | Latest Chrome, Edge, Firefox, Safari; mobile Safari and Chrome |
| Scale | Open registration, up to a measured global cap. Registration closes at the cap, and existing users are unaffected (`SECURITY.md` §13.5) |
| Languages | Complete in `en`, `ar`, `tr`. CI-enforced |

---

## 10. Feature Delivery Map

**Nothing approved is removed.** Each feature has one phase in which it's complete.

| Feature | Phase |
|---|---|
| Google sign-in, logout, session management, CSRF, rate limiting, authorization, audit basics | A (A2) |
| Accounts, double-entry ledger, income, expense, transfers, corrections, history, Quick Add, dashboard | A |
| Trips, demo participants, splitting, payer confirmation, corrections, multi-currency, FX snapshots, settlements, suggestions, confirmation, reversal, personal linking, allocation in spending, duplicate warnings, trip dashboard | B |
| Profile, preferred currency, time zone, data export | C |
| Custom categories, refunds, adjustments, budgets, Insights and analytics | C |
| Invitations, roles, full membership lifecycle, former-member scope, trip completion and archive, notification inbox, preset covers (subject to OD-13) | C |
| Expense disputes | C |
| Financial, trip, and participant reports; PDF; CSV; browser printing | C |
| Arabic with RTL; Turkish; complete translations | C |
| Full authorization and audit coverage, accessibility review | C |
| Open public registration, bot check, launch quotas, deletion tracks, privacy requests, legal pages, off-site backups, restore testing, incident procedures, production performance testing, Arabic PDF verification at scale | D |
| Android, iOS, widgets | Future |

---

## 11. Acceptance Criteria by Delivery

### 11.1 Phase A: Personal Finance Demo

- [ ] A visitor can open a synthetic sandbox, and log out and back in.
- [ ] They can create an account, record income and expenses, transfer between accounts, correct or void a transaction, and see balances update correctly.
- [ ] Quick Add works by keyboard, and repeated submits create one record.
- [ ] The dashboard's figures match the ledger, in separate per-currency blocks.
- [ ] Ledger invariants pass in CI against a real PostgreSQL, including raw-SQL rejection tests.
- [ ] Cross-user access returns 404.
- [ ] **Every visible control works.** Nothing reads as a production service.
- [ ] If published: the gate in `SECURITY.md` §14.4 has evidence.

### 11.1a Milestone A2: Authentication and Authorization

These apply to the code, whether or not a feature is switched on in the demo.

- [ ] Continue with Google works end to end against real Google, in development, with credentials created together with the owner.
- [ ] Automated tests cover state, browser binding, replay, nonce, issuer, audience, signature, expiry, malformed claims, unverified email, concurrency, and redaction, against a fake Google at the HTTP layer.
- [ ] Logout, session revocation, and suspension take effect on the next request.
- [ ] No password, registration, reset, or verification route exists.
- [ ] The same endpoints support a bearer-token client, as React Native will use.
- [ ] The authorization matrix test covers every route.

### 11.2 Phase B: Trip Mode Demo

- [ ] All of §11.1 still holds.
- [ ] Using personas, a visitor can create a trip, add an expense for another payer, confirm it as that payer, correct it, and watch balances change only on approval.
- [ ] Multi-currency expenses show original amount, rate, source, date, and base amount.
- [ ] Settlements can be recorded, confirmed, partial, and reversed. Suggested payments are labeled as suggestions.
- [ ] Scenarios A–F in `FINANCIAL_RULES.md` §20 are automated tests that pass.
- [ ] Linking and allocation produce Alex's exact approved figures.
- [ ] Trip views reveal nothing from a persona's private accounts.

### 11.3 Phase C: Complete Web MVP v1.0

- [ ] Every acceptance criterion in §3–§5 and the language table in §7 has passing tests or a recorded manual check.
- [ ] All invariants pass in CI.
- [ ] A complete trip scenario works end to end across three real accounts in a private environment, including multi-currency, correction after settlement, and PDF export.
- [ ] Every flow has been reviewed in all three languages, including Arabic RTL, by a native reader.

### 11.4 Phase D: Public Production Launch

- [ ] All of §11.3 still holds.
- [ ] Every **Mandatory** item in `SECURITY.md` §13.1 and every hosting condition in §13.6 has evidence, including the legal review.
- [ ] A restore from off-site backup has been performed and verified.
- [ ] OD-18 has been decided with advice.
- [ ] All Critical findings are closed, and open Important findings have written decisions.
- [ ] **Opening registration has explicit owner approval.**

## M6 status (2026-11-07)

Budgets, Insights and Reports are built for personal finance (single user, one currency at a time; no trip shares yet). Exports: PDF (A4, en/ar/tr), CSV (UTF-8) and print. See FINANCIAL_RULES.md §6b and §16 (as built) and API_SPEC.md §7 and §13.

## M6.1 status (2026-11-07)

Home shows this month's spending approximately in a chosen currency (display only, today's reference rate, source and time disclosed; no combined total unless every currency has a rate). Print produces the complete filtered report; a PDF that can't hold every row says PARTIAL. Budget details list expenses not counted because they're in another currency.

## Trip Mode T1 status (2026-11-09)

Built: trip lifecycle (§4.1), participants and invitations (§4.2), expenses with confirmation and corrections (§4.3, without disputes), multi-currency (§4.4), dashboard (§4.6). Not yet: settlements (§4.5), disputes, personal linking (§4.7), trip reports, demo personas (the demo build).

## Trip Mode B4 status (2026-11-10)

Settlements (§4.5) and disputes (§4.3) are built. Payments in another currency and "settle in full" are deferred; personal linking (§4.7) is B5.

## Trip Mode B5 status (2026-11-11)

Personal account integration (§4.7) and the trip report are built. Deferred: notifications for share changes and strong duplicates (shown in the trip's private card instead), the 5% cross-currency duplicate rule, and combined "create expense and link" requests.

## Everyday milestone status (2026-11-12)

Built: refunds (§3.4), quick templates, the money calendar, savings goals (new; progress is tracked, never money), subscriptions as marked recurring rules (§3.8), category groups, Trip Mode settlements in another currency (§4.5) and one-step linking (§4.7). Home adds favourite templates under Quick Add, a compact "Plans" panel (shown only when there are goals or upcoming subscriptions) and links to the new pages.

## Expansion milestone (2026-11-14, built for review)

- **Home:** a compact Quick Add (searchable currency, account and category; favourites and recent
  first; quick dates; templates, conversion, shortcuts and duplicate protection unchanged) and a
  total balances card in a chosen currency.
- **Accounts:** Total Balance at the top; each card shows its approximate value in the
  chosen currency.
- **Installments:** purchases, loans and plans already running; schedules, partial, early, extra
  and missed payments, rescheduling with history, interest and fees as stated, a linked goal, and
  an overview with due windows, the share of income, and a what-if calculator.
- **Analytics:** Insights and Reports merged, with Overview (income vs spending, savings rate,
  change from last month, commitments, a 30-day estimate, budgets), Accounts, Budgets,
  Installments and Trips tabs. Exports, filters and printing are unchanged.
- **Budgets:** delete (when no earlier month uses it), end and restore. **Subscriptions:** pause,
  resume, cancel (confirmed), payment history. **Goals:** pause, archive, restore, history, and
  following an installment plan.
- **Trips:** optional description, phases, cancel and reinstate, a shared plan (stays, travel,
  activities, notes, links), a shared budget, a private budget, and trip numbers.
- **Navigation:** no top bar on tablets and desktops; Add in the sidebar; Trips below Transactions.

## Home dashboard and removal (2026-11-15, built for review)

Home's widgets can be rearranged (drag, touch, keyboard), moved between the two columns, hidden
and shown, and reset; the layout is saved to the person's profile. Home shows Total Balance (accounts
only). Every feature offers Delete, Archive, End or Cancel as fits, with the same
confirmation and a reason when deletion isn't possible (table in FINANCIAL_RULES §15a).

### Home redesign (2026-11-09)

Owner-approved direction: an open, editorial Home in midnight navy (dark) and warm off-white (light).
Total Balance with the month's spending and income, Activity, Spending by Category (a donut, per
currency), an Upcoming Payments timeline, Budgets, and Savings Goals. Quick Add is a dialog opened
by New transaction or N. **Net worth is removed from the whole product** (FINANCIAL_RULES §7c).
Layouts are rows of up to three sections; layouts saved as two columns are read as rows.
