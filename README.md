# FinPilot

Personal finance and shared trip expense management, with financial reporting.

> **Status: under active development, not yet deployed.** Built and under review, in English, Arabic and Turkish:
>
> - **Sign-in and account:** Google sign-in (the only way in), sessions and signed-in devices, profile and preferences, privacy settings, and account deletion.
> - **Personal finance:** accounts and transfers on a double-entry ledger, transactions, categories and quick templates, budgets, recurring items and subscriptions, a calendar, debts, installments, and savings goals.
> - **Analysis:** Insights and Reports, with CSV and PDF export.
> - **Trip Mode:** shared expenses with payer confirmation, multiple currencies, settlements and disputes, trip planning and budgets.
>
> Production packaging exists, but the containers haven't been run on the server yet, and beta access control and legal pages are still missing. Nothing here is ready for real financial data. FinPilot is not a bank or payment service, and it will not move money.

## Goals

- **Personal finance:** accounts, transactions, budgets, and analytics, on a double-entry ledger.
- **Trip Mode:** shared expenses with payer confirmation, multi-currency support, and settlement tracking.
- **Reporting:** financial reports with PDF and CSV export.
- **Languages:** English, Arabic (right-to-left), and Turkish.

## Technology

| Area     | Stack                                                                   |
| -------- | ----------------------------------------------------------------------- |
| Web      | Next.js, React, TypeScript (strict), Tailwind CSS, shadcn/ui, next-intl |
| API      | Python, FastAPI, Pydantic, SQLAlchemy (sync), Alembic, psycopg          |
| Database | PostgreSQL                                                              |
| Testing  | pytest, Vitest, Testing Library, Ruff, mypy, ESLint, import-linter      |

## Project structure

```
apps/web/           Next.js app; translations are in messages/
backend/app/
  api/v1/           HTTP routes, under /api/v1
  services/         Application logic
  db/               Persistence: engine, sessions, models, role and RLS hardening
  core/             Configuration, logging, errors, request middleware
backend/migrations/ Alembic migrations
backend/tests/      Backend tests
backend/tests_e2e/  Browser tests (Playwright)
backend/tests_stack/ Tests of the running production stack, through the proxy
backend/scripts/    Database provisioning, hardening and maintenance
deploy/             Production packaging: compose, proxy, deployment scripts (see deploy/README.md)
docs/               Product, architecture, financial, security, and roadmap documents
.github/workflows/  CI on GitHub-hosted runners
```

## Prerequisites

- Node.js 24 or newer, with npm
- [uv](https://docs.astral.sh/uv/) for Python; it uses the version in `backend/.python-version`
- PostgreSQL 15 or newer. Without a database, the app still starts, `/api/v1/readyz` reports 503, and database tests are skipped

## Setup

```bash
npm install
cd backend
uv sync
```

**Database.** Create dedicated local databases and roles. Review the SQL first; it only creates, and stops if a name already exists. psql asks for your PostgreSQL administrator password itself, and the script writes `backend/.env` without printing any secret.

```bash
uv run python -m scripts.provision_local_db --print-sql   # review; connects to nothing
uv run python -m scripts.provision_local_db               # apply, in your own terminal
```

Or copy `backend/.env.example` to `backend/.env`, and point it at databases you created yourself. Never commit `.env`.

Optionally, copy `apps/web/.env.example` to `apps/web/.env.local`.

## Running locally

```bash
# Terminal 1, from backend/
uv run uvicorn app.main:create_app --factory --port 8000

# Terminal 2, from the repository root
npm run dev
```

Open <http://localhost:3000>. It redirects to your language. Use `localhost`, not `127.0.0.1`: sign-in cookies belong to the host in the Google redirect URI, so in development the web app sends `127.0.0.1` to `localhost`.

| URL                                      | Purpose                                                   |
| ---------------------------------------- | --------------------------------------------------------- |
| `http://localhost:3000/en`, `/ar`, `/tr` | Web app                                                   |
| `http://localhost:8000/api/v1/healthz`   | Liveness                                                  |
| `http://localhost:8000/api/v1/readyz`    | Readiness: database reachable, and migrations are current |
| `http://localhost:8000/api/v1/docs`      | Generated API documentation (development only)            |

## Commands

From the repository root:

| Command                                  | Does              |
| ---------------------------------------- | ----------------- |
| `npm run dev`                            | Start the web app |
| `npm run build`                          | Production build  |
| `npm run lint`                           | ESLint            |
| `npm run typecheck`                      | TypeScript        |
| `npm test`                               | Web tests         |
| `npm run format`, `npm run format:check` | Prettier          |

From `backend/`:

| Command                   | Does                                                                                   |
| ------------------------- | -------------------------------------------------------------------------------------- |
| `uv run pytest`           | Backend tests                                                                          |
| `uv run pytest tests_e2e` | Browser tests in Chrome (needs the web app running; see `backend/tests_e2e/README.md`) |
| `uv run ruff check .`     | Lint                                                                                   |
| `uv run ruff format .`    | Format                                                                                 |
| `uv run mypy`             | Type check                                                                             |
| `uv run lint-imports`     | Check that layers depend downward                                                      |
| `uv run alembic history`  | List migrations                                                                        |

## Testing with a database

Database tests run when `TEST_DATABASE_URL` is set in the environment or in `backend/.env`, and otherwise show as skipped. They refuse to run unless the database name ends in `_test` and the host is local, and they check the name again with the server after connecting. Never point them at a database you care about.

## Roadmap

| Phase | Delivers                                                             | Status              |
| ----- | -------------------------------------------------------------------- | ------------------- |
| A     | Personal finance demo with synthetic data                            | Built, under review |
| B     | Trip Mode demo with synthetic data                                   | Built, under review |
| C     | Complete web product: reports, exports, budgets, all three languages | Mostly built        |
| D     | Production readiness: packaging, server test, private beta           | In progress         |
| Later | Android and iOS apps, and widgets                                    | Planned             |

Details are in [docs/ROADMAP.md](docs/ROADMAP.md).

## Deployment

Production is designed to run as containers on one server, behind a reverse proxy, with a restricted database role and row-level security. [deploy/README.md](deploy/README.md) has the instructions and rollback steps. Nothing is deployed yet. The isolated integration test on the server is prepared but hasn't run yet (see its status line and [docs/SERVER_READINESS.md](docs/SERVER_READINESS.md)).

## Contributing and security

This is a personal project. Please don't put real financial data in issues, tests, or demos. Pull requests run on GitHub-hosted runners only.

## License

Not yet chosen. Until one is added, all rights are reserved.
