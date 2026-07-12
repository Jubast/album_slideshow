from __future__ import annotations

from custom_components.album_slideshow import immich


# ── normalize_base_url ─────────────────────────────────────────────────────

def test_normalize_strips_trailing_slash_and_api():
    assert immich.normalize_base_url("http://x:2283/") == "http://x:2283"
    assert immich.normalize_base_url("http://x:2283/api") == "http://x:2283"
    assert immich.normalize_base_url("http://x:2283/api/") == "http://x:2283"
    assert immich.normalize_base_url("http://x:2283") == "http://x:2283"


# ── build_image_url ────────────────────────────────────────────────────────

def test_build_image_url_preview():
    u = immich.build_image_url("http://x:2283", "abc", "preview")
    assert u == "http://x:2283/api/assets/abc/thumbnail?size=preview"


def test_build_image_url_fullsize():
    u = immich.build_image_url("http://x:2283", "abc", "fullsize")
    assert u == "http://x:2283/api/assets/abc/thumbnail?size=fullsize"


def test_build_image_url_original():
    u = immich.build_image_url("http://x:2283/api", "abc", "original")
    assert u == "http://x:2283/api/assets/abc/original"


def test_build_image_url_never_includes_key():
    u = immich.build_image_url("http://x:2283", "abc", "preview")
    assert "apiKey" not in u and "x-api-key" not in u


# ── _to_epoch_ms ───────────────────────────────────────────────────────────

def test_to_epoch_ms_parses_z():
    assert immich._to_epoch_ms("2022-02-06T21:51:51.000Z") == 1644184311000


def test_to_epoch_ms_parses_offset():
    assert immich._to_epoch_ms("2022-02-06T21:51:51+00:00") == 1644184311000


def test_to_epoch_ms_bad_input():
    assert immich._to_epoch_ms(None) is None
    assert immich._to_epoch_ms("") is None
    assert immich._to_epoch_ms("not a date") is None


# ── location_label ─────────────────────────────────────────────────────────

def test_location_label_city_country():
    assert immich.location_label("Lisbon", None, "Portugal") == "Lisbon, Portugal"


def test_location_label_falls_back_to_state():
    assert immich.location_label(None, "California", "USA") == "California, USA"


def test_location_label_country_only():
    assert immich.location_label(None, None, "France") == "France"


def test_location_label_none():
    assert immich.location_label(None, None, None) is None
    assert immich.location_label("", "  ", "") is None


# ── parse_search_page ──────────────────────────────────────────────────────

def test_parse_search_page_filters_and_paginates():
    payload = {
        "assets": {
            "items": [
                {"id": "1", "type": "IMAGE"},
                {"id": "2", "type": "VIDEO"},
                {"id": "3", "type": "IMAGE", "isTrashed": True},
                {"id": "4", "type": "IMAGE", "isArchived": True},
                {"id": "5", "type": "IMAGE"},
                {"type": "IMAGE"},  # no id
            ],
            "nextPage": "2",
        }
    }
    items, nxt = immich.parse_search_page(payload)
    assert [i["id"] for i in items] == ["1", "5"]
    assert nxt == 2


def test_parse_search_page_no_next():
    payload = {"assets": {"items": [{"id": "1", "type": "IMAGE"}], "nextPage": None}}
    items, nxt = immich.parse_search_page(payload)
    assert len(items) == 1 and nxt is None


def test_parse_search_page_empty():
    assert immich.parse_search_page({}) == ([], None)
    assert immich.parse_search_page(None) == ([], None)


# ── parse_asset_exif ───────────────────────────────────────────────────────

def test_parse_asset_exif_full():
    asset = {
        "localDateTime": "2022-02-06T13:51:51.000Z",
        "exifInfo": {
            "dateTimeOriginal": "2022-02-06T21:51:51+00:00",
            "latitude": 38.7,
            "longitude": -9.1,
            "city": "Lisbon",
            "country": "Portugal",
            "description": "  Sunset  ",
        },
    }
    out = immich.parse_asset_exif(asset)
    assert out["captured_at"] == 1644184311000
    assert out["latitude"] == 38.7
    assert out["longitude"] == -9.1
    assert out["location"] == "Lisbon, Portugal"
    assert out["description"] == "Sunset"


def test_parse_asset_exif_null_island_dropped():
    asset = {"exifInfo": {"latitude": 0, "longitude": 0}}
    out = immich.parse_asset_exif(asset)
    assert "latitude" not in out


def test_parse_asset_exif_empty_description_ignored():
    asset = {"exifInfo": {"description": "   "}}
    out = immich.parse_asset_exif(asset)
    assert "description" not in out


def test_parse_asset_exif_no_exif():
    assert immich.parse_asset_exif({}) == {}
    assert immich.parse_asset_exif(None) == {}


# ── parse_random ───────────────────────────────────────────────────────────

def test_parse_random_plain_list():
    payload = [
        {"id": "1", "type": "IMAGE"},
        {"id": "2", "type": "VIDEO"},
        {"id": "3", "type": "IMAGE", "isTrashed": True},
        {"id": "4", "type": "IMAGE"},
    ]
    out = immich.parse_random(payload)
    assert [i["id"] for i in out] == ["1", "4"]


def test_parse_random_wrapped_dict():
    payload = {"assets": {"items": [{"id": "1", "type": "IMAGE"}]}}
    assert [i["id"] for i in immich.parse_random(payload)] == ["1"]


def test_parse_random_empty():
    assert immich.parse_random(None) == []
    assert immich.parse_random({}) == []


# ── build_search_body ──────────────────────────────────────────────────────

def test_build_search_body_album():
    assert immich.build_search_body("album", "aid", None) == {
        "type": "IMAGE",
        "albumIds": ["aid"],
    }


def test_build_search_body_person():
    assert immich.build_search_body("person", "pid", None) == {
        "type": "IMAGE",
        "personIds": ["pid"],
    }


def test_build_search_body_favorites():
    assert immich.build_search_body("favorites", None, None) == {
        "type": "IMAGE",
        "isFavorite": True,
    }


def test_build_search_body_all():
    assert immich.build_search_body("all", None, None) == {"type": "IMAGE"}


def test_build_search_body_search_forces_image_type():
    out = immich.build_search_body(
        "search", None, {"city": "Paris", "type": "VIDEO", "isFavorite": True}
    )
    assert out == {"city": "Paris", "type": "IMAGE", "isFavorite": True}



# ── multi-person union (people source, OR) ─────────────────────────────────

import asyncio


class _FakeClient(immich.ImmichClient):
    """ImmichClient whose _post is stubbed with canned per-person pages."""

    def __init__(self, pages_by_person):
        # Skip the real __init__ (no hass/session needed for these tests).
        self._pages_by_person = pages_by_person
        self.calls = []

    async def _post(self, path, body):
        self.calls.append((path, body))
        pid = body["personIds"][0]
        page = body.get("page", 1)
        pages = self._pages_by_person.get(pid, [])
        idx = page - 1
        if idx >= len(pages):
            return {"assets": {"items": [], "total": 0, "nextPage": None}}
        items = pages[idx]
        next_page = page + 1 if idx + 1 < len(pages) else None
        return {"assets": {"items": items, "total": len(items), "nextPage": next_page}}


def _asset(aid):
    return {"id": aid, "type": "IMAGE"}


def test_people_union_ors_across_people():
    client = _FakeClient(
        {
            "p1": [[_asset("a"), _asset("b")]],
            "p2": [[_asset("c")]],
        }
    )
    out = asyncio.run(client.async_collect_assets("people", "p1,p2"))
    assert [a["id"] for a in out] == ["a", "b", "c"]


def test_people_union_dedupes_shared_assets():
    client = _FakeClient(
        {
            "p1": [[_asset("a"), _asset("shared")]],
            "p2": [[_asset("shared"), _asset("b")]],
        }
    )
    out = asyncio.run(client.async_collect_assets("people", "p1,p2"))
    assert [a["id"] for a in out] == ["a", "shared", "b"]


def test_people_union_pages_each_person():
    client = _FakeClient(
        {
            "p1": [[_asset("a")], [_asset("b")]],
        }
    )
    out = asyncio.run(client.async_collect_assets("people", "p1"))
    assert [a["id"] for a in out] == ["a", "b"]
    # One query per page; every body carries exactly one personId.
    assert all(len(c[1]["personIds"]) == 1 for c in client.calls)


def test_people_union_ignores_blank_ids():
    client = _FakeClient({"p1": [[_asset("a")]]})
    out = asyncio.run(client.async_collect_assets("people", "p1,,"))
    assert [a["id"] for a in out] == ["a"]
    assert {c[1]["personIds"][0] for c in client.calls} == {"p1"}
