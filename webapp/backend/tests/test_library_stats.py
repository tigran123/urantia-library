"""Tests for the footer stats endpoint (GET /api/library-stats)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_library_stats_exposes_format_sets(app_ctx):
    """The footer's audio/video search links build their `ext:` query from these
    arrays, so they must match the canonical sets used to compute the counts."""
    helpers, _, _ = app_ctx
    main = helpers["main"]
    c = TestClient(main.app)  # guest-reachable; no auth needed

    r = c.get("/api/library-stats")
    assert r.status_code == 200
    body = r.json()

    assert body["audio_exts"] == sorted(main._AUDIO_EXTS)
    assert body["video_exts"] == sorted(main._VIDEO_EXTS)
