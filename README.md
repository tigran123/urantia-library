# Urantia Library

A self-hosted, multi-format reading library that runs entirely in the browser.
It serves a curated collection of books, manuscripts, dictionaries, and other
reference material — readable inline (PDF, EPUB, DJVU, FB2, Markdown, HTML, and
images) with reading-progress, highlights, ratings, comments, playlists, and a
built-in feedback/contact-admin system.

> This README is for developers browsing the repository. The user-facing
> introduction to the library lives in the app itself (the **Welcome** page,
> `webapp/frontend/src/welcome/`).

## Tech stack

- **Backend** — [FastAPI](https://fastapi.tiangolo.com/) served by Uvicorn. The
  service is a single ~8400-line `main.py` with a thin supporting shell
  (`database.py`, `models.py`, `schemas.py`, `security.py`, `email_utils.py`).
- **Frontend** — Vue 3 + Vite + TypeScript + TailwindCSS single-page app, built
  to static assets and served by the backend. Hash-based routing.
- **Storage** — SQLite (via SQLAlchemy) for metadata and all per-user state,
  plus a content-addressable file vault on disk.
- **Auth** — JWT in an `access_token` cookie, gated by an in-memory session map.

## Architecture: content-addressable storage (CAS)

Every book file is hashed with BLAKE2b; the bytes live at `<BOOKS_DIR>/.data/<hash>`,
and the human-readable paths under the library tree are **symlinks** into that
vault. SQLite (`<BOOKS_DIR>/.data/db/lib.db`) holds the CAS metadata (`books`,
`book_locations`) alongside users, playlists, reading progress, annotations,
ratings, comments, and the feedback system.

`BOOKS_DIR` (default `/Books`) is the filesystem root the API serves from; every
user-supplied path is validated against it before any filesystem access.

See [`CLAUDE.md`](./CLAUDE.md) for the full architecture, security model, and
operational notes.

## Repository layout

```
webapp/
  backend/      FastAPI service (main.py + thin module shell), tests, migrations
  frontend/     Vue 3 + Vite SPA
  start.sh      launches Uvicorn (used by the systemd unit)
  *.service     systemd units (app, nightly backup, GeoIP refresh, pruning)
  *.nginx       reverse-proxy configs
CLAUDE.md       detailed architecture / ops / conventions
```

## Prerequisites

- **Python 3.12** — pinned in `webapp/backend/.python-version`; the virtualenv is
  built with [`uv`](https://github.com/astral-sh/uv), which provisions a
  standalone CPython 3.12 regardless of the host Python.
- **Node.js + npm** — for the frontend build / dev server.
- **OS packages** (not in `requirements.txt`):
  - Build deps for the `djvulibre-python` C extension: `libdjvulibre-dev`,
    `pkg-config`, `build-essential`.
  - CLI tools invoked for metadata / cover / media extraction (degrade
    gracefully if absent): `poppler-utils`, `djvulibre-bin`, `calibre`,
    `ffmpeg`.

  ```sh
  sudo apt install libdjvulibre-dev pkg-config build-essential \
                   poppler-utils djvulibre-bin calibre ffmpeg
  ```

## Running the app

The backend is launched by **`webapp/start.sh`**, never by invoking Uvicorn
directly. The script is idempotent: on first run it provisions the backend
virtualenv (`uv venv && uv pip sync requirements.txt`) and builds the frontend
(`npm ci && npm run build`) when `frontend/dist` is missing, then `exec`s
Uvicorn on `127.0.0.1:8000` — adding `--reload` when `APP_ENV=development` and
the `--root-path` prefix from `APP_ROOT_PATH`.

In every environment `start.sh` is run under the checked-in
`webapp/urantia-library.service` systemd unit, which supplies configuration via
`EnvironmentFile=…/secrets.env`. Running it through the unit is the reliable way
to start the system. Note that `start.sh` does **not** read `secrets.env`
itself, so if you launch it by hand you must load the config into the
environment first — otherwise `JWT_SECRET_KEY`, the secure-cookie flag, and SMTP
settings fall back to (broken) defaults.

```sh
# 1. Per-machine config (not committed). Set APP_ENV=development for hot-reload
#    and point BOOKS_DIR at your library root; see webapp/secrets.env.example.
cp webapp/secrets.env.example webapp/secrets.env
$EDITOR webapp/secrets.env

# 2a. Canonical: run under the systemd unit (sources secrets.env for you).
#     See CLAUDE.md for the one-time unit setup.
sudo systemctl start urantia-library.service

# 2b. …or run the script directly, loading the config into the environment:
set -a; . webapp/secrets.env; set +a
webapp/start.sh
```

The app needs a configured SQLite database to boot — see
**Database & migrations** below.

For active frontend work, run the Vite dev server (hot-module reload) against
the running backend:

```sh
cd webapp/frontend
npm run dev      # proxies /api/* → 127.0.0.1:8000
npm run build    # vue-tsc -b && vite build → frontend/dist (start.sh does this for you)
```

## Configuration

Runtime config comes from `webapp/secrets.env` (per-machine, **not** committed);
see `webapp/secrets.env.example` for the full list. Key variables:
`JWT_SECRET_KEY`, `COOKIE_SECURE`, `APP_ENV`, `APP_ROOT_PATH`, `APP_URL`,
`SMTP_USER` / `SMTP_PASSWORD` / `ADMIN_EMAIL`, and `BOOKS_DIR`.

## Database & migrations

The database is plain SQLite. The canonical schema is
`webapp/backend/lib_schema.sql`; it is versioned via `app_meta.schema_version`
and upgraded by `webapp/backend/migrate.py` (applies any pending files in
`migrations/`, taking a snapshot first). A fresh database is created from
`lib_schema.sql` and then brought up to date with `migrate.py` — the schema file
stamps a baseline version that may be older than the code expects. The backend
refuses to start when its expected schema version doesn't match the database, so
a code update without the matching migration fails loudly rather than serving
errors. (`webapp/backend/initdb.sh` bootstraps and seeds a fresh DB, but it
hardcodes the maintainer's paths and admin accounts — adapt it for your own
setup.)

## Tests

```sh
cd webapp/backend
uv venv && uv pip sync requirements.txt   # if you haven't run start.sh yet
. .venv/bin/activate
python -m pytest tests/ -q
```

The suite runs against an in-memory SQLite and stubs outbound email; it needs no
running server (and no `secrets.env`).

## Production

The same `urantia-library.service` unit runs the app in production, behind
nginx (see the `webapp/*.nginx` configs). A deploy is `git pull` → run any
pending migration (`python migrate.py`) → restart the unit. The full runbook —
backups, restore-from-backup, integrity scans, and the dev/prod unit drift — is
in [`CLAUDE.md`](./CLAUDE.md).
