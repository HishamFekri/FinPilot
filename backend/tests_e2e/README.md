# Browser regression tests

Real Chrome against the real API and the real web app. Not part of the default `pytest` run.

## What they cover

- `test_dialogs.py`: the entry dialog. Opening the refund or correction form never writes,
  cancelling never writes, Save posts exactly once, a double click posts once, the keyboard
  opens and submits, and a button that switches views is never reused as the next view's
  submit button. With that fix reverted, 6 of the 7 fail.
- `test_pages.py`: every signed-in page in English, Arabic and Turkish at nine widths from 320
  to 2560 px: one `h1`, no console errors, no sideways scroll, no server errors; Arabic
  is right-to-left; a signed-out visit shows no entries.
- `test_shell.py`: the dropdown (arrows, Home and End, typeahead, Enter, Escape, focus back on
  the button, opening under its button on the correct side in Arabic, above a dialog without
  closing it), the profile menu and its phone equivalent, the text-only wordmark, and signing
  out: to the landing page in the same language, cookies cleared, Back showing nothing private,
  and an honest message when the server can't be reached. Dan and Eve exist only to sign out.
- `test_sign_in_flow.py`: the landing page is the only way in. Google (faked at its own address)
  to the dashboard, failures and cancellations back on the landing page with a message and a
  retry, an ended session, the old sign-in URL redirecting, Back after sign-out, the API being
  down, in English, Arabic and Turkish, and the old page never appearing along the way.
- `test_home.py`: the redesigned Home. Net worth appears nowhere, New transaction (button and N) opens focused and never saves by itself, a save updates Home once, the chart's exact amounts and full table, upcoming installments, missing rates, phones, reduced motion, Arabic.
- `test_account_lifecycle.py`: downloading your data, asking to delete and undoing it, what blocks it, and the Arabic page. Fresh test accounts only.
- `test_security.py`: cookie flags from a real sign-in, protective headers, stored markup that never runs, redirects limited to the app, nothing financial in browser storage, invitation tokens kept out of requests, and a dead session after sign-out.
- `test_privacy.py`: two people each see only their own entries and accounts; trip members
  share the trip but not their accounts; a non-member can't open the trip.

## Running

1. `TEST_DATABASE_URL` in `backend/.env` must point at a local database whose name ends in
   `_test`. The suite resets and migrates it. It refuses anything else, before and after
   connecting.
2. Start the web app: `npm run dev` in `apps/web` (http://localhost:3000).
3. From `backend/`: `uv run pytest tests_e2e`

To test the production build instead: `npm run build && npx next start -p 3100` in
`apps/web`, then `E2E_WEB=http://localhost:3100 uv run pytest tests_e2e`.

Options: `E2E_WEB` (web app URL), `E2E_API_PORT` (default 8101), `E2E_BROWSER_CHANNEL`
(default `chrome`; set it empty to use Playwright's bundled Chromium), and `E2E_HEADED=1` to
watch.

## How it works

The API runs inside the test process with the unit tests' fake Google and fake exchange
rates, so nothing leaves the machine. Pages load from the web app, and the browser's
`/api/v1` requests are forwarded to the test API. Test people sign in through the fake Google.
A login route that exists only in this test process hands their cookies to the browser.
Cookies and tokens are kept in memory and never written to disk.
