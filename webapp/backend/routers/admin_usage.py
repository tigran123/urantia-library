"""Router module (extracted from main.py): admin read-only views over the
librarian audit log and the usage_events analytics tables (overview,
by-country/city/book/user/ip, timeline) plus the usage-recording kill-switch
settings. All admin-only. Moved verbatim from main.py (no logic change)."""
import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, case

import models
import schemas
import state
from database import get_db
from config import _USAGE_KINDS_ALL
from deps import require_admin
from background import _audit, _load_enabled_kinds

router = APIRouter()


def _audit_actor_dict(user: models.User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "real_name": user.real_name,
        "avatar_url": user.avatar_url,
    }


@router.get("/api/admin/audit", response_model=schemas.AuditFeed)
async def admin_audit_feed(
    page: int = 1,
    per_page: int | None = None,
    actor_user_id: int | None = None,
    action: str | None = None,
    since: str | None = None,            # ISO-8601 UTC; only entries with created_at >= this are returned
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Page-based timeline of admin actions, newest first. Symmetric with
    `/api/admin/usage/timeline` and `/api/me/activity` — Prev / Page X of N /
    Next is friendlier than scrolling through 1000+ rows under "Load more".

    `per_page` defaults to the admin's own `users.search_per_page` setting
    (same knob as Search), with a 50-row fallback when unset."""
    if per_page is None:
        per_page = int(_admin.search_per_page or 50)
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))
    # Outer join because the actor row may have been hard-deleted (FKs are
    # not enforced in this SQLite). Matches the 'actor user was deleted'
    # tolerance already in admin_audit_stats — without this, deleting a
    # user account would silently drop their entire history from the feed.
    q = (
        db.query(models.AdminAuditLog, models.User)
          .outerjoin(models.User, models.AdminAuditLog.actor_user_id == models.User.id)
          .order_by(models.AdminAuditLog.id.desc())
    )
    if actor_user_id is not None:
        q = q.filter(models.AdminAuditLog.actor_user_id == actor_user_id)
    if action:
        q = q.filter(models.AdminAuditLog.action == action)
    if since:
        q = q.filter(models.AdminAuditLog.created_at >= since)

    total = q.order_by(None).count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()

    entries = []
    for entry, user in rows:
        details = None
        if entry.details_json:
            try:
                details = json.loads(entry.details_json)
            except json.JSONDecodeError:
                details = {"_raw": entry.details_json}  # don't crash the feed over a malformed row
        if entry.actor_user_id is None:
            # System/automated event with no authenticated actor (the
            # registration lifecycle: self-service register, plus approve/reject
            # via the tokenized email links). id 0 is a sentinel — autoincrement
            # never assigns it, so it can't collide with a real user.
            actor = {
                "id": 0,
                "email": "",
                "real_name": "System",
                "avatar_url": None,
            }
        elif user is None:
            # Actor was hard-deleted. Synthesize a placeholder so the row
            # stays visible — the audit log's append-only contract takes
            # precedence over hiding orphans.
            actor = {
                "id": entry.actor_user_id,
                "email": "",
                "real_name": "(deleted user)",
                "avatar_url": None,
            }
        else:
            actor = _audit_actor_dict(user)
        entries.append({
            "id": entry.id,
            "created_at": entry.created_at,
            "actor": actor,
            "action": entry.action,
            "target_kind": entry.target_kind,
            "target_id": entry.target_id,
            "summary": entry.summary,
            "details": details,
        })

    return {
        "entries": entries,
        "page": page,
        "per_page": per_page,
        "total": int(total),
        "total_pages": (int(total) + per_page - 1) // per_page,
    }


@router.get("/api/admin/audit/stats", response_model=schemas.AuditStats)
async def admin_audit_stats(
    since: str | None = None,            # ISO-8601 UTC; default = 30 days ago
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-admin tallies grouped by action, for the leaderboard view. One
    GROUP BY query plus a single users lookup; cheap even on a busy log."""
    if since is None:
        # Match the format _now_iso() writes into admin_audit_log.created_at
        # ('YYYY-MM-DDTHH:MM:SSZ') so the lexicographic >= comparison agrees
        # with chronological order. datetime.isoformat() would produce a
        # '...+00:00' suffix that sorts BEFORE 'Z' at the boundary second.
        since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = (
        db.query(
            models.AdminAuditLog.actor_user_id,
            models.AdminAuditLog.action,
            func.count().label("n"),
        )
        .filter(models.AdminAuditLog.created_at >= since)
        .group_by(models.AdminAuditLog.actor_user_id, models.AdminAuditLog.action)
        .all()
    )
    by_actor: dict[int, dict[str, int]] = {}
    for actor_id, act, n in rows:
        by_actor.setdefault(actor_id, {})[act] = n

    users = (
        db.query(models.User)
          .filter(models.User.id.in_(by_actor.keys()))
          .all()
        if by_actor else []
    )
    user_by_id = {u.id: u for u in users}

    leaders = []
    for actor_id, totals in by_actor.items():
        u = user_by_id.get(actor_id)
        if not u:
            continue  # actor_user_id is NULL (user deleted; FK SET NULL) — skip
        leaders.append({
            "actor": _audit_actor_dict(u),
            "totals": totals,
            "total": sum(totals.values()),
        })
    leaders.sort(key=lambda r: r["total"], reverse=True)

    return {"since": since, "leaders": leaders}


# ==============================================================================
# Admin → Usage analytics (reads from usage_events)
# ==============================================================================
# All endpoints below are admin-only. The shape mirrors /api/admin/audit/* —
# `days` is a relative window (default 30), and the response is a plain dict
# (no response_model) so the surface stays easy to evolve. Queries are
# indexed via ix_usage_events_*: ts / (user_id,ts) / (ip,ts) / (hash_id,ts)
# / (kind,ts).


def _usage_since(days: int | None) -> str:
    """Return an ISO-8601 UTC timestamp `days` ago, matching the format
    written into usage_events.ts so lexicographic >= agrees with chronology."""
    d = max(1, min(int(days) if days else 30, 3650))
    return (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/api/admin/usage/overview")
async def admin_usage_overview(
    days: int = 30,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """High-level rollup for the Admin → Usage landing page: totals by kind,
    distinct users / IPs / countries seen, and a per-day count for a sparkline."""
    since = _usage_since(days)
    base = db.query(models.UsageEvent).filter(models.UsageEvent.ts >= since)

    by_kind_rows = (
        db.query(models.UsageEvent.kind, func.count())
          .filter(models.UsageEvent.ts >= since)
          .group_by(models.UsageEvent.kind)
          .all()
    )
    by_kind = {k: int(n) for k, n in by_kind_rows}

    total_events = base.count()
    unique_users = (
        db.query(func.count(func.distinct(models.UsageEvent.user_id)))
          .filter(models.UsageEvent.ts >= since)
          .filter(models.UsageEvent.user_id.isnot(None))
          .scalar() or 0
    )
    unique_ips = (
        db.query(func.count(func.distinct(models.UsageEvent.ip)))
          .filter(models.UsageEvent.ts >= since)
          .scalar() or 0
    )
    unique_countries = (
        db.query(func.count(func.distinct(models.UsageEvent.geo_country)))
          .filter(models.UsageEvent.ts >= since)
          .filter(models.UsageEvent.geo_country.isnot(None))
          .scalar() or 0
    )

    # Daily sparkline. substr(ts,1,10) extracts the YYYY-MM-DD prefix.
    daily_rows = (
        db.query(func.substr(models.UsageEvent.ts, 1, 10), func.count())
          .filter(models.UsageEvent.ts >= since)
          .group_by(func.substr(models.UsageEvent.ts, 1, 10))
          .order_by(func.substr(models.UsageEvent.ts, 1, 10))
          .all()
    )
    daily = [{"date": d, "count": int(n)} for d, n in daily_rows]

    return {
        "since": since,
        "days": days,
        "total_events": int(total_events),
        "by_kind": by_kind,
        "unique_users": int(unique_users),
        "unique_ips": int(unique_ips),
        "unique_countries": int(unique_countries),
        "daily": daily,
    }


@router.get("/api/admin/usage/by-country")
async def admin_usage_by_country(
    days: int = 30,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Country leaderboard: events / unique IPs / unique users per ISO-2."""
    since = _usage_since(days)
    rows = (
        db.query(
            models.UsageEvent.geo_country,
            func.count().label("n"),
            func.count(func.distinct(models.UsageEvent.ip)).label("ips"),
            func.count(func.distinct(models.UsageEvent.user_id)).label("users"),
        )
        .filter(models.UsageEvent.ts >= since)
        .group_by(models.UsageEvent.geo_country)
        .order_by(func.count().desc())
        .all()
    )
    return {
        "since": since,
        "days": days,
        "countries": [
            {"country": c or "UNKNOWN", "events": int(n), "unique_ips": int(ips), "unique_users": int(users)}
            for c, n, ips, users in rows
        ],
    }


@router.get("/api/admin/usage/by-city")
async def admin_usage_by_city(
    country: str,
    days: int = 30,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Cities within a given ISO-2 country code, by event count."""
    since = _usage_since(days)
    rows = (
        db.query(
            models.UsageEvent.geo_city,
            func.count().label("n"),
            func.count(func.distinct(models.UsageEvent.ip)).label("ips"),
        )
        .filter(models.UsageEvent.ts >= since)
        .filter(models.UsageEvent.geo_country == country)
        .group_by(models.UsageEvent.geo_city)
        .order_by(func.count().desc())
        .all()
    )
    return {
        "since": since,
        "days": days,
        "country": country,
        "cities": [
            {"city": c or "UNKNOWN", "events": int(n), "unique_ips": int(ips)}
            for c, n, ips in rows
        ],
    }


@router.get("/api/admin/usage/by-book")
async def admin_usage_by_book(
    days: int = 30,
    limit: int = 50,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Most-opened books in the window. Joined to books for the title/author;
    a NULL join row means the book was deleted (ON DELETE SET NULL keeps the
    event but drops the hash_id), so those rows are filtered out.

    `signed_in_readers` counts distinct user_id values (NULLs ignored by SQL),
    while `guest_ips` counts distinct ip values from rows where user_id IS NULL.
    Splitting them avoids the misleading 'opened by 0 readers' that you'd see
    when the book was only opened by guests."""
    since = _usage_since(days)
    limit = max(1, min(int(limit), 500))
    rows = (
        db.query(
            models.UsageEvent.hash_id,
            models.Book.title,
            models.Book.author,
            func.count().label("opens"),
            func.count(func.distinct(models.UsageEvent.user_id)).label("signed_in_readers"),
            func.count(func.distinct(case(
                (models.UsageEvent.user_id.is_(None), models.UsageEvent.ip),
                else_=None,
            ))).label("guest_ips"),
        )
        .outerjoin(models.Book, models.Book.id == models.UsageEvent.hash_id)
        .filter(models.UsageEvent.ts >= since)
        .filter(models.UsageEvent.kind == "book_open")
        .filter(models.UsageEvent.hash_id.isnot(None))
        .group_by(models.UsageEvent.hash_id)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )
    return {
        "since": since,
        "days": days,
        "books": [
            {
                "hash_id": h,
                "title": title,
                "author": author,
                "opens": int(opens),
                "signed_in_readers": int(readers),
                "guest_ips": int(guests),
            }
            for h, title, author, opens, readers, guests in rows
        ],
    }


@router.get("/api/admin/usage/by-user")
async def admin_usage_by_user(
    days: int = 30,
    limit: int = 100,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-user activity rollup. Guest rows (user_id IS NULL) are excluded;
    those are surfaced via /by-ip instead."""
    since = _usage_since(days)
    limit = max(1, min(int(limit), 1000))
    rows = (
        db.query(
            models.UsageEvent.user_id,
            models.User.email,
            models.User.real_name,
            func.count().label("total"),
            func.count(func.distinct(case((models.UsageEvent.kind == "book_open", models.UsageEvent.hash_id), else_=None))).label("books"),
            func.max(models.UsageEvent.ts).label("last_seen"),
        )
        .join(models.User, models.User.id == models.UsageEvent.user_id)
        .filter(models.UsageEvent.ts >= since)
        .group_by(models.UsageEvent.user_id)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )
    return {
        "since": since,
        "days": days,
        "users": [
            {
                "user_id": uid,
                "email": email,
                "real_name": real_name,
                "total_events": int(total),
                "unique_books_opened": int(books),
                "last_seen": last_seen,
            }
            for uid, email, real_name, total, books, last_seen in rows
        ],
    }


@router.get("/api/admin/usage/by-ip")
async def admin_usage_by_ip(
    days: int = 30,
    country: str | None = None,
    limit: int = 100,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-IP rollup, optionally filtered to a single country. One row per IP
    with first_seen / last_seen extremes."""
    since = _usage_since(days)
    limit = max(1, min(int(limit), 1000))
    q = (
        db.query(
            models.UsageEvent.ip,
            models.UsageEvent.geo_country,
            models.UsageEvent.geo_city,
            func.count().label("n"),
            func.count(func.distinct(models.UsageEvent.user_id)).label("users"),
            func.min(models.UsageEvent.ts).label("first_seen"),
            func.max(models.UsageEvent.ts).label("last_seen"),
        )
        .filter(models.UsageEvent.ts >= since)
    )
    if country:
        q = q.filter(models.UsageEvent.geo_country == country)
    rows = (
        q.group_by(models.UsageEvent.ip)
         .order_by(func.count().desc())
         .limit(limit)
         .all()
    )
    return {
        "since": since,
        "days": days,
        "country": country,
        "ips": [
            {
                "ip": ip,
                "country": c,
                "city": city,
                "events": int(n),
                "unique_users": int(users),
                "first_seen": first_seen,
                "last_seen": last_seen,
            }
            for ip, c, city, n, users, first_seen, last_seen in rows
        ],
    }


@router.get("/api/admin/usage/settings")
async def admin_usage_settings_get(
    _admin: models.User = Depends(require_admin),
):
    """Return the current set of usage_event kinds being recorded, and the
    full list of recognised kinds so the UI can render every checkbox even
    if a future kind is added in code but not yet enabled."""
    return {
        "enabled_kinds": sorted(state._enabled_kinds),
        "all_kinds": list(_USAGE_KINDS_ALL),
    }


@router.put("/api/admin/usage/settings")
async def admin_usage_settings_put(
    payload: schemas.UsageSettingsUpdate,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replace the enabled-kinds set. Silently drops unknown kind names —
    the persisted set is always a subset of _USAGE_KINDS_ALL."""
    new_set = frozenset(v for v in payload.enabled_kinds if v in _USAGE_KINDS_ALL)
    payload_json = json.dumps(sorted(new_set))

    row = db.query(models.AppMeta).filter(
        models.AppMeta.key == "usage_events_enabled_kinds"
    ).first()
    old_value = row.value if row else None
    if row:
        row.value = payload_json
    else:
        db.add(models.AppMeta(key="usage_events_enabled_kinds", value=payload_json))

    _audit(
        db, _admin, "usage.kinds_update",
        target_kind="settings",
        target_id="usage_events_enabled_kinds",
        summary=f"Usage recording: {', '.join(sorted(new_set)) or '(none)'}",
        details={"old": old_value, "new": payload_json},
    )
    db.commit()

    _load_enabled_kinds()
    return {
        "enabled_kinds": sorted(state._enabled_kinds),
        "all_kinds": list(_USAGE_KINDS_ALL),
    }


@router.get("/api/admin/usage/timeline")
async def admin_usage_timeline(
    page: int = 1,
    per_page: int | None = None,
    user: str | None = None,           # accepts numeric user_id or full email; admin disambiguates
    ip: str | None = None,
    hash_id: str | None = None,
    kind: str | None = None,
    country: str | None = None,
    since: str | None = None,
    until: str | None = None,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Raw event feed, newest first, page-based pagination (the matching
    Audit endpoint still uses cursor + 'Load more' — we use page-numbers here
    for symmetry with #/account/activity, which the operator preferred).

    All filters compose with AND. `per_page` defaults to the admin's own
    `users.search_per_page` setting (same knob as Search) so pagination is
    consistent across all admin tables.

    `user` accepts either a numeric id (matches usage_events.user_id) or an
    email address (case-insensitive exact match against users.email). A
    nonexistent email yields an empty result, not a 404."""
    if per_page is None:
        per_page = int(_admin.search_per_page or 100)
    page = max(page, 1)
    per_page = max(1, min(per_page, 500))
    q = (
        db.query(models.UsageEvent, models.User)
          .outerjoin(models.User, models.User.id == models.UsageEvent.user_id)
          .order_by(models.UsageEvent.id.desc())
    )
    if user:
        token = user.strip()
        token_lc = token.lower()
        if token_lc in ("guest", "(guest)"):
            q = q.filter(models.UsageEvent.user_id.is_(None))
        elif token.isdigit():
            q = q.filter(models.UsageEvent.user_id == int(token))
        else:
            # Email: resolve once, then filter by the integer user_id so the
            # SQLite indexes on usage_events still apply. A lookup that finds
            # nothing forces a deliberately empty result set rather than
            # filtering by NULL (which would silently match guest rows).
            resolved = db.query(models.User.id).filter(
                func.lower(models.User.email) == token_lc
            ).scalar()
            if resolved is None:
                q = q.filter(models.UsageEvent.id == -1)
            else:
                q = q.filter(models.UsageEvent.user_id == resolved)
    if ip:
        q = q.filter(models.UsageEvent.ip == ip)
    if hash_id:
        q = q.filter(models.UsageEvent.hash_id == hash_id)
    if kind:
        q = q.filter(models.UsageEvent.kind == kind)
    if country:
        q = q.filter(models.UsageEvent.geo_country == country)
    if since:
        q = q.filter(models.UsageEvent.ts >= since)
    if until:
        q = q.filter(models.UsageEvent.ts <= until)

    total = q.count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()

    entries = []
    for ev, user in rows:
        extra = None
        if ev.extra_json:
            try:
                extra = json.loads(ev.extra_json)
            except json.JSONDecodeError:
                extra = {"_raw": ev.extra_json}
        entries.append({
            "id": ev.id,
            "ts": ev.ts,
            "user_id": ev.user_id,
            "user_email": user.email if user else None,
            "session_jti": ev.session_jti,
            "ip": ev.ip,
            "user_agent": ev.user_agent,
            "geo_country": ev.geo_country,
            "geo_city": ev.geo_city,
            "kind": ev.kind,
            "path": ev.path,
            "hash_id": ev.hash_id,
            "extra": extra,
        })

    return {
        "entries": entries,
        "page": page,
        "per_page": per_page,
        "total": int(total),
        "total_pages": (int(total) + per_page - 1) // per_page,
    }
