from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.album_slideshow import coordinator as c


# ── _media_node_is_image ───────────────────────────────────────────────────

def test_is_image_by_media_class():
    assert c._media_node_is_image("image", None) is True


def test_is_image_by_mime():
    assert c._media_node_is_image(None, "image/jpeg") is True


def test_video_class_is_rejected():
    assert c._media_node_is_image("video", None) is False


def test_video_mime_is_rejected_even_if_class_missing():
    assert c._media_node_is_image(None, "video/mp4") is False


def test_directory_is_not_an_image():
    assert c._media_node_is_image("directory", None) is False


def test_unknown_is_not_an_image():
    assert c._media_node_is_image(None, None) is False


# ── _normalize_resolved_url ────────────────────────────────────────────────

def test_absolute_url_passes_through():
    u = "https://immich.example.com/api/asset/1/thumbnail"
    assert c._normalize_resolved_url(u, "http://homeassistant.local:8123") == u


def test_relative_url_is_prefixed_with_base():
    out = c._normalize_resolved_url(
        "/media/local/a.jpg?authSig=abc", "http://homeassistant.local:8123"
    )
    assert out == "http://homeassistant.local:8123/media/local/a.jpg?authSig=abc"


def test_relative_url_without_base_is_unchanged():
    assert c._normalize_resolved_url("/media/local/a.jpg", "") == "/media/local/a.jpg"


def test_empty_url_is_returned_as_is():
    assert c._normalize_resolved_url("", "http://x") == ""


# ── _browse_media_source (recursive tree walk) ─────────────────────────────

def _node(cid, *, media_class=None, mime=None, can_expand=False, title=None):
    return SimpleNamespace(
        media_content_id=cid,
        media_class=media_class,
        media_content_type=mime,
        can_expand=can_expand,
        title=title,
    )


class _FakeMediaSource:
    """Minimal media_source stand-in: a map of content_id -> children list."""

    def __init__(self, tree: dict[str, list]):
        self.tree = tree

    async def async_browse_media(self, hass, content_id):
        return SimpleNamespace(children=self.tree.get(content_id, []))

    async def async_resolve_media(self, hass, content_id, target=None):
        return SimpleNamespace(url=f"/media/{content_id}.jpg", mime_type="image/jpeg")


def _stub_coord(media_source=None):
    coord = c.AlbumCoordinator.__new__(c.AlbumCoordinator)
    coord.hass = object()
    return coord


def test_browse_collects_image_leaves_and_recurses():
    tree = {
        "root": [
            _node("img1", media_class="image", title="One"),
            _node("folderA", media_class="directory", can_expand=True),
            _node("vid1", media_class="video"),
        ],
        "folderA": [
            _node("img2", mime="image/png", title="Two"),
            _node("img3", media_class="image", title="Three"),
        ],
    }
    fake = _FakeMediaSource(tree)
    coord = _stub_coord()
    collected: list = []
    asyncio.run(coord._browse_media_source(fake, "root", collected, 0))
    ids = [cid for cid, _ in collected]
    titles = [t for _, t in collected]
    assert ids == ["img1", "img2", "img3"]
    assert titles == ["One", "Two", "Three"]


def test_browse_respects_item_cap():
    children = [_node(f"img{i}", media_class="image") for i in range(20)]
    fake = _FakeMediaSource({"root": children})
    coord = _stub_coord()
    collected: list = []
    orig = c._MEDIA_SOURCE_MAX_ITEMS
    try:
        c._MEDIA_SOURCE_MAX_ITEMS = 5
        asyncio.run(coord._browse_media_source(fake, "root", collected, 0))
    finally:
        c._MEDIA_SOURCE_MAX_ITEMS = orig
    assert len(collected) == 5


def test_browse_respects_depth_cap():
    # A chain of nested expandable folders deeper than the depth cap.
    tree = {}
    for i in range(20):
        tree[f"f{i}"] = [_node(f"f{i + 1}", media_class="directory", can_expand=True)]
    tree["f20"] = [_node("deep_img", media_class="image")]
    fake = _FakeMediaSource(tree)
    coord = _stub_coord()
    collected: list = []
    asyncio.run(coord._browse_media_source(fake, "f0", collected, 0))
    # Depth cap stops recursion before reaching the deep image.
    assert collected == []


# ── _resolve_media ─────────────────────────────────────────────────────────

def test_resolve_media_returns_url_and_mime():
    fake = _FakeMediaSource({})
    coord = _stub_coord()
    out = asyncio.run(coord._resolve_media(fake, "abc"))
    assert out == ("/media/abc.jpg", "image/jpeg")


def test_resolve_media_falls_back_to_two_arg_signature():
    class _OldMediaSource:
        async def async_resolve_media(self, hass, content_id):  # no target arg
            return SimpleNamespace(url="/media/old.jpg", mime_type="image/jpeg")

    coord = _stub_coord()
    out = asyncio.run(coord._resolve_media(_OldMediaSource(), "x"))
    assert out == ("/media/old.jpg", "image/jpeg")


def test_resolve_media_returns_none_on_error():
    class _BrokenMediaSource:
        async def async_resolve_media(self, hass, content_id, target=None):
            raise RuntimeError("nope")

    coord = _stub_coord()
    assert asyncio.run(coord._resolve_media(_BrokenMediaSource(), "x")) is None
