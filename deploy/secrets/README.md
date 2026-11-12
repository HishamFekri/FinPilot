# Secrets

Created on the server by `scripts/init-secrets.sh --generate`. Everything in this folder except this file is ignored by Git and must never be copied into an image, a log, a chat, or a backup that leaves the server unencrypted.

| File | Used by | Content |
|---|---|---|
| `postgres_password` | `db` | The PostgreSQL superuser's password, used only inside the database network |
| `admin_database_url` | `db-provision` | Superuser URL, for creating the two roles once |
| `owner_role_password`, `runtime_role_password` | `db-provision` | The two roles' passwords, sent to PostgreSQL only as SCRAM verifiers |
| `owner_database_url` | `migrate`, `db-apply`, `backup`, `restore` | The schema owner |
| `database_url` | `api`, `db-verify` | The restricted runtime role |
| `google_client_secret` | `api` | The production Google OAuth client secret. **You put it here.** Empty means the API refuses to start |
