# Geotab Fleet Analytics

Self-hosted fleet analytics for Geotab data, built with Python, FastHTML, PostgreSQL, SQLAlchemy, Alembic, APScheduler, Pandas-ready transformation services, and Plotly dashboards. It replaces Power BI with a browser-based app that can run locally or on Railway.

## Architecture

Geotab API -> Sync Worker -> Transformation Layer -> PostgreSQL -> Analytics Layer -> FastHTML Dashboard

The code is separated by responsibility:

- `app/geotab`: JSON-RPC Geotab client and raw payload transforms.
- `app/services`: reusable sync orchestration, incremental metadata, upserts, rollback handling, sync logs.
- `app/models`: normalized SQLAlchemy ORM tables.
- `app/analytics`: reporting metrics reused by dashboards and APIs.
- `app/dashboards`: Plotly chart helpers.
- `app/jobs`: APScheduler setup.
- `migrations`: Alembic schema migrations.
- `tests`: model, analytics, sync, and API tests.

## Local Setup

1. Create a virtual environment with Python 3.12+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Start PostgreSQL.

```bash
docker compose -f docker/docker-compose.yml up -d
```

3. Configure environment variables.

```bash
cp .env.example .env
```

Set:

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/geotab_analytics
SESSION_SECRET=<long-random-secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<local-admin-password>
GEOTAB_DATABASE=<GEOTAB_DATABASE_NAME>
GEOTAB_USERNAME=<GEOTAB_USERNAME>
GEOTAB_PASSWORD=<GEOTAB_PASSWORD>
GEOTAB_SERVER=my.geotab.com
```

Credentials are read only from environment variables. Do not commit `.env`.

If you are not ready to sync from Geotab locally, set `SCHEDULER_ENABLED=false`.

4. Run migrations.

```bash
alembic upgrade head
```

5. Start the app.

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

## Admin Authentication

The app uses signed session cookies, CSRF tokens on state-changing forms, and Passlib bcrypt password verification.

Password precedence:

1. `ADMIN_PASSWORD_HASH` is always used when set.
2. Plain `ADMIN_PASSWORD` is accepted only when `ENVIRONMENT` is not `production`.
3. In production, plain `ADMIN_PASSWORD` is ignored even if set.

Generate a production password hash:

```bash
python -c "from app.auth.security import hash_password; print(hash_password('your-password'))"
```

Session cookies are `HttpOnly`, use `SameSite=Lax`, and are marked `Secure` when `ENVIRONMENT=production`. Sessions expire after `SESSION_MAX_AGE_SECONDS` (default `28800`, 8 hours). Successful login rotates the session ID.

## Geotab Configuration

Use a dedicated **MyGeotab service account** with API access. The app authenticates with Geotab JSON-RPC `Authenticate` using:

- `GEOTAB_DATABASE`
- `GEOTAB_USERNAME`
- `GEOTAB_PASSWORD`
- `GEOTAB_SERVER` (usually `my.geotab.com`)

The client reuses the returned session credentials for subsequent `Get` calls and re-authenticates only when Geotab reports an invalid or expired session. An API key is **not** required for standard MyGeotab service-account login.

Required when `SCHEDULER_ENABLED=true`:

```bash
GEOTAB_DATABASE=<GEOTAB_DATABASE_NAME>
GEOTAB_USERNAME=<GEOTAB_USERNAME>
GEOTAB_PASSWORD=<GEOTAB_PASSWORD>
GEOTAB_SERVER=my.geotab.com
GEOTAB_TIMEOUT_SECONDS=30
```

Incremental entities use `fromDate` based on `sync_metadata.last_sync_timestamp`.

## Scheduled Sync Architecture

APScheduler registers these jobs, all defaulting to every 15 minutes:

- `sync_vehicles`
- `sync_drivers`
- `sync_trips`
- `sync_logs`
- `sync_faults`

Each sync:

1. Reads the last successful timestamp from `sync_metadata`.
2. Requests only newer records where supported.
3. Validates and transforms raw Geotab payloads.
4. Upserts on stable Geotab identifiers.
5. Writes a `sync_logs` record.
6. Commits on success or rolls back and records failure.

Configure with:

```bash
SYNC_INTERVAL_MINUTES=15
SYNC_LOOKBACK_HOURS=24
SCHEDULER_ENABLED=true
```

## Dashboards

Available pages:

- `/`: Executive dashboard with fleet cards, mileage trend, fuel trend, utilization ranking.
- `/vehicles`: vehicle filters, daily mileage, speed distribution, trip history, recent GPS points.
- `/drivers`: driver ranking, distance driven, trips completed, performance table.
- `/maintenance`: fault frequency, fault types, current faults.
- `/fleet-map`: latest vehicle locations and status.

The UI uses FastHTML routes, HTMX partial updates, Plotly interactive charts, dark mode, and responsive CSS.

## Internal JSON API

Authenticated API routes:

- `/api/fleet-summary`
- `/api/vehicles`
- `/api/drivers`
- `/api/trips`
- `/api/faults`

These routes are intentionally internal but structured for future integrations.

## Railway Deployment

1. Create a Railway project.
2. Add a PostgreSQL service.
3. Deploy this repository with the included `Dockerfile` and `railway.json`.
4. Set environment variables:

```bash
DATABASE_URL=${{Postgres.DATABASE_URL}}
ENVIRONMENT=production
SESSION_SECRET=<long-random-secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<bcrypt-hash>
GEOTAB_DATABASE=<GEOTAB_DATABASE_NAME>
GEOTAB_USERNAME=<GEOTAB_USERNAME>
GEOTAB_PASSWORD=<GEOTAB_PASSWORD>
GEOTAB_SERVER=my.geotab.com
SCHEDULER_ENABLED=true
SYNC_INTERVAL_MINUTES=15
```

The container startup command runs:

```bash
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Railway health checks use `/health`.

For persistence, rely on Railway PostgreSQL storage for application data. Do not store operational data on the container filesystem.

## Credentials And Connections To Edit

Do not commit real credentials. Use `.env` for local development and Railway environment variables for production.

### Railway Environment Variables

Set these in the Railway service variables:

```bash
ENVIRONMENT=production
DATABASE_URL=${{Postgres.DATABASE_URL}}
SESSION_SECRET=<long-random-secret>
SESSION_MAX_AGE_SECONDS=28800
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<bcrypt-hash>

GEOTAB_DATABASE=<GEOTAB_DATABASE_NAME>
GEOTAB_USERNAME=<GEOTAB_USERNAME>
GEOTAB_PASSWORD=<GEOTAB_PASSWORD>
GEOTAB_SERVER=my.geotab.com
GEOTAB_TIMEOUT_SECONDS=30

SYNC_INTERVAL_MINUTES=15
SYNC_LOOKBACK_HOURS=24
SCHEDULER_ENABLED=true
LOG_LEVEL=INFO
```

### Local Environment File

Create a local `.env` from the example:

```bash
cp .env.example .env
```

Edit:

```text
.env
```

Minimum local values:

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/geotab_analytics
SESSION_SECRET=<long-random-secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<local-admin-password>

GEOTAB_DATABASE=<GEOTAB_DATABASE_NAME>
GEOTAB_USERNAME=<GEOTAB_USERNAME>
GEOTAB_PASSWORD=<GEOTAB_PASSWORD>
GEOTAB_SERVER=my.geotab.com
```

### Geotab Connection

The app connects to Geotab through `app/geotab/client.py` using JSON-RPC `Authenticate` against `https://{GEOTAB_SERVER}/apiv1`. Configure the connection only through environment variables:

```bash
GEOTAB_DATABASE=<GEOTAB_DATABASE_NAME>
GEOTAB_USERNAME=<GEOTAB_USERNAME>
GEOTAB_PASSWORD=<GEOTAB_PASSWORD>
GEOTAB_SERVER=my.geotab.com
```

Create a dedicated MyGeotab service account for this app. Use that account's database name, username, and password. If your Geotab tenant uses a regional or custom server, replace `my.geotab.com`.

### Admin Login

For local development, set `ADMIN_PASSWORD` when `ENVIRONMENT` is not `production`.

For Railway production, set `ENVIRONMENT=production`, a strong `SESSION_SECRET`, and `ADMIN_PASSWORD_HASH`. Plain `ADMIN_PASSWORD` is ignored in production.

Generate a password hash:

```bash
python -c "from app.auth.security import hash_password; print(hash_password('your-password'))"
```

Set the output as:

```bash
ADMIN_PASSWORD_HASH=<generated-hash>
```

Do not set a plain `ADMIN_PASSWORD` in production.

### Railway PostgreSQL Connection

Add a Railway PostgreSQL service and set:

```bash
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

The app accepts Railway's `postgres://` or `postgresql://` format and normalizes it to the `psycopg` SQLAlchemy driver internally. No manual host, username, password, or port split is required when using Railway's injected `DATABASE_URL`.

### Railway CLI Access

Railway SSH or shell access is only needed for operations, not normal app configuration.

Useful commands:

```bash
railway login
railway link
railway shell
railway logs
```

Inside `railway shell`, useful maintenance commands include:

```bash
alembic upgrade head
python -c "from app.auth.security import hash_password; print(hash_password('new-password'))"
```

### Files That Reference Connections

Review these files when changing deployment or connection behavior:

```text
.env.example
app/config.py
app/geotab/client.py
app/database/session.py
Dockerfile
railway.json
docker/docker-compose.yml
```

## Testing

```bash
pytest
```

The tests cover:

- ORM model creation.
- Analytics calculations.
- Incremental sync and duplicate prevention.
- API route authentication behavior.
- Config validation and auth hardening behavior.

## Troubleshooting

- App fails to start with config validation errors: set a strong `SESSION_SECRET` and `ADMIN_PASSWORD_HASH` in production.
- Scheduler fails to start without Geotab credentials: set `GEOTAB_DATABASE`, `GEOTAB_USERNAME`, and `GEOTAB_PASSWORD`, or set `SCHEDULER_ENABLED=false` until Geotab is configured.
- `Geotab credentials are not configured`: confirm `GEOTAB_DATABASE`, `GEOTAB_USERNAME`, and `GEOTAB_PASSWORD` are set.
- Login fails in production: set `ADMIN_PASSWORD_HASH`; plain `ADMIN_PASSWORD` is only accepted outside production.
- No dashboard data: run migrations, confirm the scheduler is enabled, and inspect `sync_logs`.
- Database connection errors: Railway may expose `postgres://`; the app normalizes it to `postgresql+psycopg://`.
- API rate limits or timeouts: the Geotab client retries transient failures with exponential backoff and logs Railway-friendly messages to stdout.
