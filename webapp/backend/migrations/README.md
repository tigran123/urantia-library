# Migrations

Each schema change goes in here as a numbered file. The runner is
`../migrate.py`; it reads `app_meta.schema_version` and applies every
file in this directory whose 4-digit prefix is higher than the stored
value, in numerical order, then bumps the version.

## Naming

`NNNN_short_slug.sql` or `NNNN_short_slug.py`

- `NNNN` is a four-digit, append-only sequence number. Never renumber.
- The slug is for humans; only the prefix matters to the runner.
- `0001` is implicit — it's the state described by `../lib_schema.sql`,
  stamped at the bottom of that file. The first real migration file is
  `0002_*`.

## Choosing SQL vs Python

- `.sql` — pure DDL/DML. Executed via `executescript`. Don't include
  `BEGIN` / `COMMIT` unless you specifically need them; the runner
  doesn't wrap SQL files in a transaction because `executescript`
  commits implicitly anyway.
- `.py` — anything that needs to read the filesystem, compute values,
  or branch on data. Must expose:
  ```python
  def upgrade(conn: sqlite3.Connection) -> None:
      ...
  ```
  Don't call `conn.commit()` yourself unless you have a reason; the
  runner commits once after `upgrade` returns.

## Workflow when adding a migration

1. Pick the next free number: `ls migrations/` then add 1.
2. Write `migrations/NNNN_what_it_does.{sql,py}`.
3. Bump `EXPECTED_SCHEMA_VERSION` in `../database.py` to the same number.
4. Test on dev (`python migrate.py` then restart the service).
5. Commit both files together so a `git pull` on prod always sees a
   matching version constant + migration file.

The runner refuses to start if there's a numbering gap, so a missing
`git pull` on prod fails loudly instead of silently skipping a step.
