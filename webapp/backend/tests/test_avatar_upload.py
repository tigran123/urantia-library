"""upload_avatar normalizes the image to a 512x512 JPEG and deletes the user's
previous avatar file (they used to accumulate forever). The interactive crop is
client-side; this pins the server-side guarantee.
"""
from __future__ import annotations

import io
import os

from PIL import Image


def _png_bytes(size=(300, 300), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_avatar_normalized_to_512_jpeg(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    monkeypatch.setattr(main, "AVATAR_DIR", str(tmp_path), raising=False)
    helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")

    # A non-square source must still come back square (center-crop-to-cover).
    r = c.post("/api/users/me/avatar",
               files={"file": ("pic.png", _png_bytes((300, 400)), "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["avatar_url"]
    assert url.startswith("/api/avatars/") and url.endswith(".jpg")

    path = os.path.join(str(tmp_path), os.path.basename(url))
    with Image.open(path) as im:
        assert im.format == "JPEG"
        assert im.size == (512, 512)


def test_avatar_replaces_and_deletes_old_file(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    monkeypatch.setattr(main, "AVATAR_DIR", str(tmp_path), raising=False)
    helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")

    r1 = c.post("/api/users/me/avatar", files={"file": ("a.png", _png_bytes(), "image/png")})
    assert r1.status_code == 200, r1.text
    first = os.path.basename(r1.json()["avatar_url"])
    assert os.path.isfile(os.path.join(str(tmp_path), first))

    r2 = c.post("/api/users/me/avatar",
                files={"file": ("b.png", _png_bytes(color=(10, 10, 200)), "image/png")})
    assert r2.status_code == 200, r2.text
    second = os.path.basename(r2.json()["avatar_url"])
    assert second != first
    assert os.path.isfile(os.path.join(str(tmp_path), second))
    # The previous avatar file was cleaned up.
    assert not os.path.exists(os.path.join(str(tmp_path), first))


def test_avatar_delete_clears_url_and_removes_file(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    monkeypatch.setattr(main, "AVATAR_DIR", str(tmp_path), raising=False)
    helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")

    up = c.post("/api/users/me/avatar", files={"file": ("a.png", _png_bytes(), "image/png")})
    assert up.status_code == 200, up.text
    name = os.path.basename(up.json()["avatar_url"])
    assert os.path.isfile(os.path.join(str(tmp_path), name))

    r = c.delete("/api/users/me/avatar")
    assert r.status_code == 200, r.text
    assert r.json()["avatar_url"] is None
    # File on disk is gone; UI falls back to initials.
    assert not os.path.exists(os.path.join(str(tmp_path), name))


def test_avatar_delete_is_idempotent_when_none(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    monkeypatch.setattr(main, "AVATAR_DIR", str(tmp_path), raising=False)
    helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")

    r = c.delete("/api/users/me/avatar")  # no avatar set
    assert r.status_code == 200, r.text
    assert r.json()["avatar_url"] is None


def test_avatar_rejects_non_image(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    monkeypatch.setattr(main, "AVATAR_DIR", str(tmp_path), raising=False)
    helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")

    r = c.post("/api/users/me/avatar", files={"file": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 400
    # Nothing left behind on a rejected upload.
    assert os.listdir(str(tmp_path)) == []
