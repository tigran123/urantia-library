# Project Description for AI

This file provides guidance to any AI agent when working with code in this repository.

## Repository layout

`/Books` is the root of a personal book library. The actual code project lives at `/Books/urantia-library` (a separate git repo); everything else under `/Books` (`Grammars/`, `Law/`, etc.) is library content browsed by that app.

- `/Books/urantia-library/webapp/backend` — FastAPI service. `main.py` is the single ~8400-line entry point; the rest of the module is a thin shell (`database.py`, `models.py`, `schemas.py`, `security.py`, `email_utils.py`).
- `/Books/urantia-library/webapp/frontend` — Vue 3 + Vite + TailwindCSS SPA, served as static assets in production. Multi-format reader: PDF (pdfjs-dist), EPUB (epubjs), DJVU, FB2, Markdown (highlight.js), HTML, plain images.
- `/Books/urantia-library/webapp/generate-thumbnails.py` — one-shot tooling that generates thumbnails for image directories; not run by the webapp.
- `/Books/urantia-library/webapp/backend/reimport_orphans.py` — recovery tool used after restoring `lib.db` from a backup; see "Restoring from a backup" below.
- `/Books/.data/` — the content-addressable vault (see Architecture). Do not treat the hex-named files there as garbage; they are the canonical book files.

## Architecture: Hybrid Content-Addressable Storage (CAS)

This is the non-obvious thing to understand before touching the backend.

1. Every book file in the library is hashed with BLAKE2b. The physical bytes live at `/Books/.data/<hash>`. The human-readable paths under `/Books/Grammars/...`, `/Books/Law/...` are **symlinks** into `.data`.
2. SQLite (`/Books/.data/db/lib.db`) holds the CAS metadata plus all per-user state. Tables fall into three groups:
   - **CAS core** — `books` (metadata keyed by BLAKE2b hash, `id`), `book_locations` (`symlink_path` → `hash_id`, many-to-one).
   - **Auth + presence** — `users`, `registration_requests`. Active JWT sessions live only in memory (see Auth below).
   - **Per-user state** — `playlists`, `playlist_items` (which supersede the dormant legacy `favorites` and `directory_favorites` tables), `reading_progress`, `book_ratings`, `book_comments`, `annotations`. `playlist_items` references books by `hash_id` (so they survive file moves) and directories by path.
   - **Feedback / contact-admin** — `feedback_threads`, `feedback_recipients` (empty = broadcast; non-empty = directed), `feedback_messages` (kind ∈ `message|admin|internal|status`), `feedback_attachments`, `user_notification_prefs`, `admin_feedback_settings` (singleton row id=1, seeded on startup).
   - **Misc** — `app_meta` (small KV store; currently used as the throttle clock for moderation/feedback digests).
3. Asset directories under `.data/`:
   - `/Books/.data/covers/<hash>.jpg` — book covers, served by `GET /api/covers/{hash_id}`.
   - `/Books/.data/avatars/` — user-uploaded avatars, mounted via `StaticFiles` at `/api/avatars`.
   - `/Books/.data/feedback_attachments/` — overridable via `FEEDBACK_ATTACHMENT_DIR`. Auto-created on startup.
4. `BOOKS_DIR` (default `/Books`) is the filesystem root the API serves from. All path inputs are joined to it and then checked with `os.path.abspath(...).startswith(BOOKS_DIR)` to block traversal — preserve that check on any new endpoint that takes a `path` param.

This layout was originally bootstrapped from an existing tree by a now-deleted CAS migration script (walked the tree, hashed each file, moved it into `.data/<hash>`, replaced the original with a relative symlink). New books today enter through the admin upload flow in `main.py` (`_extract_upload_metadata`, `_extract_cover_to`), and `reimport_orphans.py` reuses those same helpers to recover vault files whose `books` row was lost across a DB restore. Metadata for both paths follows a "Hierarchy of Truth": `.htaccess` `AddDescription` lines → `pdfinfo`/`djvused`/`ebook-meta` → filename fallback (sets `needs_review=True`).

`database.py` opens SQLite with `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`, and `foreign_keys=ON`, and registers a Python-based `lower()` UDF so case-insensitive search via `func.lower()` works on Cyrillic and other non-ASCII text (SQLite's built-in only folds ASCII A–Z). Don't replace it. The FK pragma matters: SQLite now actually enforces the declared `ON DELETE CASCADE` / `ON DELETE SET NULL` actions, so delete semantics should live in the schema/models, not in ad-hoc application-side cleanup.

## Running the webapp

Both dev and prod machines run the backend via the systemd unit `urantia-library.service`, which executes `webapp/start.sh` as user `tigran` with `WorkingDirectory=/Books/` and `EnvironmentFile=/Books/urantia-library/webapp/secrets.env`. `start.sh` `exec`s uvicorn on `127.0.0.1:8000` with `--no-access-log` and adds `--reload` **only when `APP_ENV=development`**. `--root-path` is passed only when `APP_ROOT_PATH` is set to a non-empty value other than `/`; both empty and `/` mean "no prefix" and either spelling is accepted in `secrets.env`. Passing `--root-path /` literally would make uvicorn prepend `/` to `scope.path`, so `request.url.path` would come back as `//api/...`. The SPA mount point is controlled by `APP_ROOT_PATH`: prod sets it to `/library` (matching nginx, which still serves the app under `/library/` and strips that prefix before forwarding to uvicorn), dev leaves it unset. `vite.config.ts` reads the same env var as its `base` so built asset URLs line up with the uvicorn root-path.

`secrets.env` provides per-machine config and is **not** committed. A reference template lives at `webapp/secrets.env.example`. Variables consumed:
- `JWT_SECRET_KEY` — used by `security.py`; falls back to a hardcoded dev string if unset.
- `COOKIE_SECURE` — `false` on dev (so the auth cookie works over plain http on the LAN); secure-by-default on prod.
- `APP_ENV` — `development` enables `--reload`; anything else means prod.
- `APP_ROOT_PATH` — uvicorn `--root-path` and Vite `base`. Prod: `/library`. Dev: unset.
- `APP_URL` — public base URL inserted into outgoing emails (digests, approvals).
- `SMTP_USER`, `SMTP_PASSWORD`, `ADMIN_EMAIL` — outbound email + admin notification address.
- `BOOKS_DIR` — overrides the `/Books` default (rarely needed).

The unit file checked into the repo (`webapp/urantia-library.service`) is the prod variant with `After=network-online.target nginx.service` + `Requires=nginx.service`. On the dev machine those two lines are commented out in the installed copy at `/etc/systemd/system/urantia-library.service` — that is the *only* expected drift between dev and prod unit files. If you edit the repo copy, remember the installed copy on dev is hand-patched; don't blindly `cp` it across.

`webapp/start.sh` is idempotent: it creates `backend/.venv` via `uv` and runs `npm ci && npm run build` if `frontend/dist` is missing, then `exec`s uvicorn. The built SPA is served by `app.mount("/", StaticFiles(directory="../frontend/dist", html=True))` at the bottom of `main.py` — there is no SPA fallback handler, which is why the frontend uses hash routing.

### Running the backend manually (rare)

The systemd unit is the normal path even in dev. If you do need to run uvicorn by hand (e.g. attaching a debugger), source `secrets.env` first so `JWT_SECRET_KEY`, `COOKIE_SECURE`, `SMTP_*`, and `APP_URL` are all set — otherwise JWTs fall back to the hardcoded dev secret in `security.py`, the auth cookie defaults to `Secure=true` (which the browser drops over http), and any code path that sends mail will blow up.

```
cd /Books/urantia-library/webapp/backend
uv venv && uv pip sync requirements.txt
. .venv/bin/activate
set -a; . ../secrets.env; set +a
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

`BOOKS_DIR` defaults to `/Books` if unset.

### Local frontend dev

```
cd /Books/urantia-library/webapp/frontend
npm run dev      # vite dev server, proxies /api/* → 127.0.0.1:8000
npm run build    # vue-tsc -b && vite build → frontend/dist
```

`vite.config.ts` sets `base: process.env.APP_ROOT_PATH || '/'` and proxies `/api` to the backend (no prefix-stripping — the dev backend defaults to root). `vite.config.ts.local` is the alternate config that emulates the prod nginx layout by proxying `/library/*` and stripping the prefix; ignore unless you specifically need to reproduce a prod-path bug locally.

### Tests

A pytest suite lives at `webapp/backend/tests/`. `conftest.py` swaps `database.engine`/`SessionLocal` to an in-memory SQLite (StaticPool), forces `main` to re-import against it, enables `PRAGMA foreign_keys=ON` on the test engine, and monkey-patches `email_utils._send_email_multipart` so digests/updates are captured into a list instead of going through SMTP. The `client_for(email)` helper in `conftest.py` is the canonical template for *any* code that mints a JWT — it shows how to register the `jti` in `_active_sessions` so the auth deps accept the token. `pytest`/`httpx` are listed in `requirements.in` for that reason; the server doesn't import them at runtime.

### Database

The application DB is plain SQLite at `/Books/.data/db/lib.db`. SQLAlchemy creates tables via `models.Base.metadata.create_all` on startup, but the canonical schema (including `books`/`book_locations`, which used to be populated by the now-deleted CAS migration script) is `webapp/backend/lib_schema.sql`.

`webapp/backend/initdb.sh` wipes `lib.db`, applies the schema, and seeds the admin user — useful for tests or starting a fresh tree. There is no longer a one-shot script that re-hashes existing files into `.data/` and populates `book_locations`; on a populated tree, the only supported recovery flow is "Restoring from a backup" below.

### Schema migrations

Schema is versioned via `app_meta.schema_version`. The runner is `webapp/backend/migrate.py`; it reads the stored version, applies every file in `webapp/backend/migrations/` whose 4-digit prefix is higher than that value (in order), then bumps the version. `.sql` files are run via `executescript`; `.py` files must expose `def upgrade(conn: sqlite3.Connection) -> None`. The runner takes one online-safe snapshot to `/Books/.data/db/pre-migrate-<UTC>.bak` before applying anything.

The launch baseline (what `lib_schema.sql` produces) is version `1`. Real migrations start at `0002_*`. When you add one, also bump `EXPECTED_SCHEMA_VERSION` in `webapp/backend/database.py` to the same number, in the same commit — the backend's startup check refuses to boot when the code constant doesn't match the DB row, so a `git pull` without a corresponding `python migrate.py` fails loudly instead of serving 500s from missing columns. Schema version `9` is the FK-enforcement change: the app now enables SQLite foreign-key enforcement at connect time, and `0009_fk_actions.py` rebuilds the affected tables so their `ON DELETE` actions match the intended behavior on upgraded databases too.

### Backups

`/Books/urantia-library/webapp/backend/scripts/nightly_backup.sh` uses the SQLite online backup API to write `/Books/.data/db/backups/lib-YYYYMMDD.db` and prunes to the 14 newest. Driven by the systemd timer `urantia-library-backup.timer` (daily 03:00 UTC), both units checked into `webapp/`.

### Restoring from a backup

After replacing `lib.db` with one of the dated snapshots, the vault under `/Books/.data/` will contain book files for any books added during the gap that have no matching `books` row. Recovery order:

```
cp /Books/.data/db/backups/lib-YYYYMMDD.db /Books/.data/db/lib.db
cd /Books/urantia-library/webapp/backend
. .venv/bin/activate
python migrate.py                                          # bring schema_version current
python reimport_orphans.py --scan                          # list orphans
python reimport_orphans.py --apply                         # park them in /Books/Unsorted/ at clearance=100, needs_review=TRUE
sudo systemctl restart urantia-library.service
```

`reimport_orphans.py` uses the same metadata/cover helpers as the upload flow (`_extract_upload_metadata`, `_extract_cover_to` in `main.py`). It can only recover what's derivable from the file bytes themselves — hand-edited titles/authors, the original `book_locations.symlink_path`, and per-user state (playlists, annotations, ratings, reading_progress, comments) for the orphan books are gone with the replaced DB. The admin moves each orphan to its proper topic via the existing `POST /api/admin/books/move` UI.

### Deploying to prod

```
ssh prod
cd /Books/urantia-library
git pull
cd webapp/backend
. .venv/bin/activate
python migrate.py                                          # snapshot + apply any pending migrations
sudo systemctl restart urantia-library.service
sudo systemctl status urantia-library.service              # confirm startup check passed
```

If the unit fails to restart, the log shows the expected vs actual `schema_version` — either run the missing migration or roll back the pull.

## Conventions worth knowing

- The backend is one big `main.py`. New routes go there; don't introduce a router-package split unless the user asks.
- Auth is JWT in an `access_token` cookie, gated by an in-memory session map (`_active_sessions` in `main.py`, keyed by the JWT's `jti` claim). Routes that need a user `Depends(get_current_user)` — it already 401s on missing/invalid/inactive/terminated, don't re-check. Guest-reachable routes use `Depends(get_optional_user)` and treat `None` as anonymous clearance 0. `create_access_token` returns `(token, jti, expires_at)`; any new code path that mints a token must also register the jti in `_active_sessions` — see `/api/login` and `tests/conftest.py:client_for` for the template.
- A backend restart wipes `_active_sessions`, so every browser is bounced to `/login` on its next API call. That's by design — it's the same mechanism the Admin → Users → Sessions panel uses to terminate one row. The SPA detects termination without waiting for a user-driven request: `App.vue` polls `/api/me` every 30s (and on tab focus) as a liveness heartbeat. The axios interceptor in `api.ts` exempts `/me` from auto-redirect (anonymous guests legitimately 401 there), so the heartbeat itself decides whether a 401 means "you were just terminated."
- The `/api/browse` and `/api/files/{path:path}` endpoints both take user-controlled paths. Always join against `BOOKS_DIR` and validate with the `abspath().startswith(BOOKS_DIR)` guard before any filesystem op. The format-specific content endpoints (`/api/fb2-content`, `/api/md-content`, `/api/html-content`, `/api/text-preview`, `/api/djvu-*`, etc.) all go through `assert_can_read_path` for the same reason.
- The browse listing explicitly skips the entries in `_TOPDIR_SKIPLIST` — currently `{".claude", ".antigravitycli", ".vscode", ".data", "CLAUDE.md", "GEMINI.md", "urantia-library"}`. Add new top-level infra dirs/files to that set in `main.py` if you create them, otherwise they'll appear as "books" in the UI. The same set is also used by `/api/library-stats` when counting top-level directories.
- `/api/browse` also filters by `clearance` for non-admins: it hides any subdirectory whose entire subtree has no readable book, and 403s direct access to such a path. The check goes through `_accessible_locations_query` in `main.py` (one indexed prefix scan on `book_locations`). Any new endpoint that lists directories or paths must reuse it, otherwise the topic structure leaks via directory names.
- All access-denied responses use `detail="Forbidden"` — never `"Insufficient clearance"` or any wording that names the clearance system. A non-admin should not be able to deduce from API responses that clearance exists. Keep 403 for all clearance-related denials (consistent with `assert_can_read_path`).
- Background work (admin integrity jobs, feedback/comment digest emails, per-user notification emails) uses `asyncio.create_task` for in-process state mutation and `threading.Thread(..., daemon=True).start()` for fire-and-forget SMTP. Digest throttling is race-safe via a conditional `UPDATE` on `app_meta` — see `_maybe_send_feedback_digest` and `_maybe_send_moderation_digest`. If you add a new background email path, copy that pattern; don't `await` SMTP inline.
- The integrity verification subsystem (`/api/admin/integrity/*`) keeps job state in the in-process `INTEGRITY_JOBS` dict; only one job runs at a time (subsequent `POST` returns 409 with the running job's id). A backend restart loses in-flight jobs, same trade-off as the session map.
- Annotations have a per-format `anchor` JSON shape (`pdf` / `epub` / `html`) — see `AnnotationAnchor` in `frontend/src/api.ts` and the per-format anchor helpers under `frontend/src/lib/anchors/`. Private annotations live with `status='approved'` (the field only gates *public* visibility); public ones revert to `pending` on every edit so they re-enter moderation.
- Comments and ratings: one top-level comment per (user, book) — enforced by a partial unique index `ix_book_comments_one_toplevel`. Replies are unrestricted but one level deep. Ratings are unmoderated; comments and public annotations go through admin approval.
- With FK enforcement enabled, SQLite now honors the declared `ON DELETE` actions. Prefer encoding row-lifecycle behavior in `lib_schema.sql` + `models.py` and mirroring it in the migration when needed; keep explicit delete code only for non-database side effects (files, symlinks, covers, attachment blobs, in-memory state). If a FK uses `SET NULL`, make the ORM/schema/API types nullable all the way out to the frontend.
- Playlists: every user has one non-deletable `kind='bookshelf'` playlist (partial unique index `ix_playlists_one_bookshelf`; auto-created lazily by `_get_or_create_bookshelf`, race-safe via IntegrityError-and-requery). `playlist_items` hold either a book (`book_hash_id`) or a directory (`dir_path`), one-per-target via the partial unique indexes `ix_playlist_items_book`/`ix_playlist_items_dir`. The legacy `favorites`/`directory_favorites` tables are dormant — don't write to them. Playlists are a **second clearance-filtered surface**: `_serialize_items` / `_dir_item_visible` / `_collage_items` reapply the same gating as `/api/browse` (gated books and empty directories are silently dropped for non-owners), and the public `GET /api/shared/{token}` view (guest-reachable via `get_optional_user`) must keep filtering per viewer — never bypass it. Share tokens are stable across private↔public toggles (going private just 404s the shared endpoint; the token is kept so re-sharing reactivates the same link).
- Frontend uses `createWebHashHistory` (hash routing) because the SPA is mounted under `/library/` in prod and the backend doesn't do SPA fallback. Hash routing is what insulates the router from the nginx/uvicorn prefix dance — keep it.
- Pyright/pyrefly are intentionally disabled (`pyrightconfig.json`, `.vscode/settings.json`) — don't add type-checking to CI without asking.
