from __future__ import annotations

from custom_components.album_slideshow import nextcloud as nc


# ── normalize_base_url ─────────────────────────────────────────────────────

def test_normalize_strips_trailing_slash():
    assert nc.normalize_base_url("http://cloud.example.com/") == "http://cloud.example.com"
    assert nc.normalize_base_url("http://cloud.example.com") == "http://cloud.example.com"


# ── parse_share_link ───────────────────────────────────────────────────────

def test_parse_share_link_pretty_url():
    out = nc.parse_share_link("https://cloud.example.com/apps/photos/public/AbC123")
    assert out == ("https://cloud.example.com", "AbC123")


def test_parse_share_link_trailing_slash():
    out = nc.parse_share_link("https://cloud.example.com/apps/photos/public/AbC123/")
    assert out == ("https://cloud.example.com", "AbC123")


def test_parse_share_link_index_php_form():
    out = nc.parse_share_link(
        "https://cloud.example.com/index.php/apps/photos/public/AbC123"
    )
    assert out == ("https://cloud.example.com", "AbC123")


def test_parse_share_link_subdirectory_install():
    out = nc.parse_share_link(
        "https://example.com/nextcloud/apps/photos/public/AbC123"
    )
    assert out == ("https://example.com/nextcloud", "AbC123")


def test_parse_share_link_with_query_string():
    out = nc.parse_share_link(
        "https://cloud.example.com/apps/photos/public/AbC123?foo=bar"
    )
    assert out == ("https://cloud.example.com", "AbC123")


def test_parse_share_link_rejects_non_matching_url():
    assert nc.parse_share_link("https://cloud.example.com/s/AbC123") is None
    assert nc.parse_share_link("not a url") is None
    assert nc.parse_share_link("") is None
    assert nc.parse_share_link(None) is None


# ── build_image_url / build_preview_url ────────────────────────────────────

def test_build_image_url():
    url = nc.build_image_url("https://cloud.example.com", "AbC123", "photo one.jpg")
    assert url == (
        "https://cloud.example.com/remote.php/dav/photospublic/AbC123/photo%20one.jpg"
    )


def test_build_preview_url_default_size():
    url = nc.build_preview_url("https://cloud.example.com", "AbC123", "456")
    assert url == (
        "https://cloud.example.com/index.php/apps/photos/api/v1/publicPreview/456"
        "?token=AbC123&x=1024&y=1024"
    )


def test_build_preview_url_custom_size():
    url = nc.build_preview_url("https://cloud.example.com", "AbC123", "456", px=256)
    assert "x=256&y=256" in url


# ── parse_propfind_response ─────────────────────────────────────────────────

_ROOT = "https://cloud.example.com/remote.php/dav/photospublic/AbC123/"

_MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:response>
    <d:href>/remote.php/dav/photospublic/AbC123/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/photospublic/AbC123/subfolder/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <oc:fileid>111</oc:fileid>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/photospublic/AbC123/photo1.jpg</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontenttype>image/jpeg</d:getcontenttype>
        <d:getcontentlength>123456</d:getcontentlength>
        <d:getlastmodified>Mon, 12 Jan 2024 10:00:00 GMT</d:getlastmodified>
        <oc:fileid>456</oc:fileid>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/photospublic/AbC123/notes.txt</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontenttype>text/plain</d:getcontenttype>
        <d:getcontentlength>10</d:getcontentlength>
        <oc:fileid>789</oc:fileid>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/photospublic/AbC123/screenshot.PNG</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>999</d:getcontentlength>
        <oc:fileid>321</oc:fileid>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


def test_parse_propfind_skips_root_and_folders_keeps_images():
    items = nc.parse_propfind_response(_MULTISTATUS, _ROOT)
    filenames = [it["filename"] for it in items]
    assert filenames == ["photo1.jpg", "screenshot.PNG"]


def test_parse_propfind_extracts_fields():
    items = nc.parse_propfind_response(_MULTISTATUS, _ROOT)
    photo = next(it for it in items if it["filename"] == "photo1.jpg")
    assert photo["content_type"] == "image/jpeg"
    assert photo["size"] == 123456
    assert photo["file_id"] == "456"
    assert photo["mtime_ms"] == 1705053600000


def test_parse_propfind_falls_back_to_extension_when_no_content_type():
    items = nc.parse_propfind_response(_MULTISTATUS, _ROOT)
    shot = next(it for it in items if it["filename"] == "screenshot.PNG")
    assert shot["content_type"] is None
    assert shot["file_id"] == "321"


def test_parse_propfind_handles_malformed_xml():
    assert nc.parse_propfind_response("not xml", _ROOT) == []


def test_parse_propfind_handles_empty_multistatus():
    empty = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"></d:multistatus>'
    assert nc.parse_propfind_response(empty, _ROOT) == []


# Shape verified against a real Nextcloud server (28.x-era): the root
# collection's ``<d:response>`` carries *two* propstats - one ``200`` with
# just ``resourcetype``, and a second ``404 Not Found`` for props a folder
# can't answer (``getcontenttype``/``getcontentlength``/``getlastmodified``/
# ``oc:fileid``). A parser that naively grabs the first ``<d:prop>`` it finds
# per response (instead of picking the ``200`` propstat) would silently work
# for this shape too, but one that grabs the *last* prop block, or merges
# both, would not - this pins the real multi-propstat structure so that
# regression stays caught.
_REAL_SHAPE_MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns"><d:response><d:href>/remote.php/dav/photospublic/AbC123/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat><d:propstat><d:prop><d:getcontenttype/><d:getcontentlength/><d:getlastmodified/><oc:fileid/></d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat></d:response><d:response><d:href>/remote.php/dav/photospublic/AbC123/12345-20260518_190350.jpg</d:href><d:propstat><d:prop><d:getcontenttype>image/jpeg</d:getcontenttype><d:getcontentlength>4424803</d:getcontentlength><d:getlastmodified>Mon, 18 May 2026 17:03:51 GMT</d:getlastmodified><d:resourcetype/><oc:fileid>12345</oc:fileid></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>
"""


def test_parse_propfind_real_server_shape_multi_propstat_root():
    root = "https://cloud.example.com/remote.php/dav/photospublic/AbC123/"
    items = nc.parse_propfind_response(_REAL_SHAPE_MULTISTATUS, root)
    assert len(items) == 1
    photo = items[0]
    assert photo["filename"] == "12345-20260518_190350.jpg"
    assert photo["content_type"] == "image/jpeg"
    assert photo["size"] == 4424803
    assert photo["file_id"] == "12345"
