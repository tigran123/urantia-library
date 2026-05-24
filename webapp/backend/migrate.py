#!/usr/bin/env python3
"""Apply pending DB schema migrations.

Reads app_meta.schema_version, then applies every file in
webapp/backend/migrations/ whose 4-digit prefix is higher than the
stored value, in order. Bumps schema_version after each successful
migration. Takes one pre-migration snapshot before applying anything
(online-safe with WAL).

File naming: NNNN_short_slug.{sql,py}
  - NNNN is a 4-digit, append-only sequence number.
  - .sql files are executed via sqlite3.Connection.executescript.
  - .py files must define `def upgrade(conn: sqlite3.Connection) -> None`.
    The runner commits after upgrade(conn) returns.
"""
import importlib.util
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")
DB_PATH = os.path.join(BOOKS_DIR, ".data", "db", "lib.db")
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SNAPSHOT_DIR = Path(BOOKS_DIR) / ".data" / "db"
MIGRATION_RE = re.compile(r"^(\d{4})_[A-Za-z0-9_\-]+\.(sql|py)$")


def _die(msg: str) -> None:
    print(f"migrate: {msg}", file=sys.stderr)
    sys.exit(1)


def _read_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM app_meta WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        _die(
            "app_meta has no 'schema_version' row. Stamp the baseline with:\n"
            f"  sqlite3 {DB_PATH} \"INSERT OR IGNORE INTO app_meta(key,value) VALUES ('schema_version','1');\""
        )
    try:
        return int(row[0])
    except (TypeError, ValueError):
        _die(f"app_meta.schema_version is not an integer: {row[0]!r}")
        raise  # unreachable; for type-checkers


def _discover_pending(current: int) -> list[tuple[int, Path]]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    pending: list[tuple[int, Path]] = []
    seen: set[int] = set()
    for entry in MIGRATIONS_DIR.iterdir():
        if not entry.is_file():
            continue
        if entry.name in {"README.md", "__init__.py"} or entry.name.startswith("."):
            continue
        m = MIGRATION_RE.match(entry.name)
        if not m:
            _die(f"unrecognized migration filename: {entry.name}")
            return []  # unreachable
        num = int(m.group(1))
        if num in seen:
            _die(f"duplicate migration number {num:04d}")
        seen.add(num)
        if num > current:
            pending.append((num, entry))
    pending.sort()
    expected = current + 1
    for num, path in pending:
        if num != expected:
            _die(
                f"migration sequence gap: have {num:04d} but expected {expected:04d} "
                f"(at {path.name}). Did you `git pull` before running?"
            )
        expected += 1
    return pending


def _snapshot(src: sqlite3.Connection) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = SNAPSHOT_DIR / f"pre-migrate-{ts}.bak"
    if dest_path.exists():
        _die(f"snapshot path already exists: {dest_path}")
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()
    return dest_path


def _load_py_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        _die(f"could not load {path}")
        raise  # unreachable
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    if not os.path.exists(DB_PATH):
        _die(f"database not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        current = _read_version(conn)
        pending = _discover_pending(current)
        if not pending:
            print(f"migrate: schema_version={current}, no pending migrations.")
            return
        print(
            f"migrate: schema_version={current}, "
            f"applying {len(pending)} migration(s): "
            + ", ".join(f"{n:04d}" for n, _ in pending)
        )
        snap = _snapshot(conn)
        print(f"migrate: pre-migration snapshot at {snap}")
        for num, path in pending:
            print(f"migrate: applying {path.name} ...")
            if path.suffix == ".sql":
                with open(path) as f:
                    conn.executescript(f.read())
            else:
                mod = _load_py_module(path)
                if not hasattr(mod, "upgrade"):
                    _die(f"{path.name}: missing upgrade(conn) function")
                mod.upgrade(conn)
                conn.commit()
            conn.execute(
                "UPDATE app_meta SET value=? WHERE key='schema_version'",
                (str(num),),
            )
            conn.commit()
            print(f"migrate: schema_version -> {num}")
        print("migrate: done.")
    except sqlite3.Error as e:
        print(f"migrate: sqlite error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
