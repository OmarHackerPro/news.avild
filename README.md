# news.avild.com

Security News & Threat Intelligence Platform — FastAPI backend, Nginx frontend, PostgreSQL (auth), OpenSearch (news content).

---

## Architecture

| Service     | Technology          | Role                                                  |
| ----------- | ------------------- | ----------------------------------------------------- |
| Frontend    | Nginx               | Serves static HTML pages + CSS/JS assets              |
| Backend     | FastAPI (Python)    | REST API for news data and user auth                  |
| Auth DB     | PostgreSQL 16       | Users, feed source config, JWT auth                   |
| Content DB  | OpenSearch 2.x      | News articles and raw feed snapshots (full-text search + faceted filtering) |

Nginx proxies `/api/` requests to the FastAPI backend and serves all HTML pages directly as static files.

Data split:
- **PostgreSQL** — `users`, `feed_sources` (transactional, relational)
- **OpenSearch** — `news_articles`, `raw_feed_snapshots` (searchable, high-volume)

---

## Prerequisites

Git, Docker Desktop, and Python 3.13 are required. Run the script for your OS to install them all at once:

**Windows** (PowerShell as Administrator):

```powershell
Set-ExecutionPolicy Bypass -Scope Process; .\install-prereqs.ps1
```

**macOS / Linux**:

```bash
bash install-prereqs.sh
```

The scripts skip anything that's already installed. After running, **restart your machine** before continuing (Docker Desktop needs it on Windows).

---

## OpenSearch Setup

OpenSearch must be running before starting the backend. The backend creates the
`news_articles` and `raw_feed_snapshots` indexes automatically on first startup.

### Create a dedicated app user on the OpenSearch machine

SSH into the OpenSearch machine and run the following commands. Replace
`ADMIN_PASSWORD` with the actual admin (or Vaqif) account password, and choose
a strong password for `kiber_app`.

```bash
# 1. Create a role with access limited to the two app indexes
curl -k -u admin:ADMIN_PASSWORD \
  -X PUT "https://localhost:9200/_plugins/_security/api/roles/kiber_app" \
  -H 'Content-Type: application/json' \
  -d '{
    "cluster_permissions": ["cluster_composite_ops", "cluster_monitor"],
    "index_permissions": [{
      "index_patterns": ["news_articles", "raw_feed_snapshots"],
      "allowed_actions": ["indices_all"]
    }]
  }'

# 2. Create the app user
curl -k -u admin:ADMIN_PASSWORD \
  -X PUT "https://localhost:9200/_plugins/_security/api/internalusers/kiber_app" \
  -H 'Content-Type: application/json' \
  -d '{
    "password": "STRONG_APP_PASSWORD",
    "backend_roles": [],
    "attributes": {}
  }'

# 3. Map the user to the role
curl -k -u admin:ADMIN_PASSWORD \
  -X PUT "https://localhost:9200/_plugins/_security/api/rolesmapping/kiber_app" \
  -H 'Content-Type: application/json' \
  -d '{
    "users": ["kiber_app"]
  }'

# 4. Verify the user can connect
curl -k -u kiber_app:STRONG_APP_PASSWORD "https://localhost:9200/_cluster/health"
```

### Expose port 9200 for remote access

Port 9200 is bound to `localhost` only by default. Pick one option:

**Option A — SSH tunnel** (no config changes, good for dev):
```bash
# Run this on your local machine / app server before starting the backend
ssh -L 9200:localhost:9200 -N user@81.17.98.185
```
Use `OPENSEARCH_URL=https://localhost:9200` in `.env`.

**Option B — Bind to all interfaces** (production, add a firewall rule):

On the OpenSearch machine, edit `/etc/opensearch/opensearch.yml`:
```yaml
network.host: 0.0.0.0
```

Then restrict port 9200 in the firewall to allow only your backend server's IP:
```bash
ufw allow from <BACKEND_SERVER_IP> to any port 9200
```

Use `OPENSEARCH_URL=https://81.17.98.185:9200` in `.env`.

> **Note:** The OpenSearch instance uses self-signed demo TLS certificates.
> The backend client has `verify_certs=False` for this reason. For production,
> replace the demo certs with a proper CA-signed certificate.

---

## Running the Full Stack (Docker)

```bash
cp .env.example .env
# Edit .env — set OPENSEARCH_URL, OPENSEARCH_USER, OPENSEARCH_PASSWORD
docker compose up --build
```

This builds and starts all three services. The site is available at <http://localhost>

To stop:

```bash
docker compose down
```

---

## Local Dev (Backend Only)

### 1. Clone the repo

```bash
git clone https://github.com/OmarHackerPro/kiber.git
cd kiber
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Set up your environment file

```bash
cp .env.example .env
# Edit .env — fill in DATABASE_URL, OPENSEARCH_URL, OPENSEARCH_USER, OPENSEARCH_PASSWORD
```

### 4. Start the database

```bash
docker compose up -d db
```

### 5. Run database migrations

```bash
alembic upgrade head
```

### 6. (One-time) Migrate existing articles to OpenSearch

If you have existing data in PostgreSQL:

```bash
python scripts/migrate_to_opensearch.py --dry-run  # preview row counts
python scripts/migrate_to_opensearch.py            # run the migration
```

### 7. Start the dev server

```bash
uvicorn main:app --reload
```

API available at <http://localhost:8000> — OpenSearch indexes are created automatically on startup.

---

## Key URLs

| URL                                   | Description                       |
| ------------------------------------- | --------------------------------- |
| <http://localhost/>                   | Home page                         |
| <http://localhost/category>           | Category / topic page             |
| <http://localhost/search>             | Search                            |
| <http://localhost/entity>             | Threat actor / entity page        |
| <http://localhost/preferences>        | My Stack — source preferences     |
| <http://localhost/rss-config>         | RSS feed configuration            |
| <http://localhost/webhooks>           | Webhook settings                  |
| <http://localhost/api/news/>          | News feed (JSON)                  |
| <http://localhost/api/news/{slug}>    | Single news item by slug          |
| <http://localhost:8000/docs>          | Swagger UI (backend dev only)     |
| <http://localhost:8000/redoc>         | ReDoc (backend dev only)          |
| <http://81.17.98.185:5601>            | OpenSearch Dashboards             |

---

## Project Structure

```text
kiber/
├── main.py                  # FastAPI app entry point
├── requirements.txt
├── docker-compose.yml       # Full stack: frontend + backend + db
├── Dockerfile.backend       # FastAPI container
├── Dockerfile.frontend      # Nginx container (serves static HTML)
├── install-prereqs.ps1      # One-command prereq installer (Windows)
├── install-prereqs.sh       # One-command prereq installer (macOS/Linux)
├── .env.example             # Environment template — copy to .env
├── nginx/
│   └── nginx.conf           # Nginx reverse proxy + static file config
├── alembic.ini              # Alembic config
├── alembic/
│   └── versions/            # Database migration files
├── app/
│   ├── api/routes/          # API route handlers
│   ├── core/config.py       # App settings (reads from .env)
│   ├── db/
│   │   ├── base.py          # SQLAlchemy declarative base
│   │   ├── models/          # ORM models (users, feed_sources)
│   │   ├── opensearch.py    # OpenSearch client + index mappings
│   │   └── session.py       # PostgreSQL engine and session factory
│   ├── models/              # Pydantic response schemas
│   └── ingestion/           # RSS feed ingestion pipeline
├── scripts/
│   ├── ingest_feeds.py          # Run the feed ingestion pipeline
│   ├── migrate_to_opensearch.py # One-time PG → OpenSearch data migration
│   ├── reparse_snapshots.py     # Re-normalize stored raw feed snapshots
│   └── seed_sources.py          # Seed feed_sources table
├── templates/               # HTML pages (served by Nginx)
└── static/                  # CSS, JS, assets (served at /static/)
```

---

## Adding a new migration

After changing a model in `app/db/models/`:

```bash
alembic revision --autogenerate -m "describe what changed"
alembic upgrade head
```

Commit the generated file in `alembic/versions/` so teammates can apply it too.

---

## Dropping the old PostgreSQL news tables

After verifying OpenSearch has all data and the app is stable, run the cleanup migration to drop the now-unused `news_articles` and `raw_feed_snapshots` PostgreSQL tables:

```bash
# Verify counts first
curl -k -u kiber_app:PASSWORD "https://localhost:9200/news_articles/_count"
curl -k -u kiber_app:PASSWORD "https://localhost:9200/raw_feed_snapshots/_count"

# Then drop the tables
alembic upgrade head
```
