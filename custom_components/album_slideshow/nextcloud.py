"""Nextcloud Photos (collaborative album, public link) client and helpers.

Talks to the ``photospublic`` WebDAV collection that the Nextcloud Photos app
registers for a public album share - no session, API key, or Basic-auth is
involved; the share token embedded in the URL path is the only credential
the server checks (verified against the current ``nextcloud/photos`` server
source: ``PublicAlbumAuthBackend`` unconditionally authenticates any request
against a valid token).

API shape:
- ``PROPFIND /remote.php/dav/photospublic/{token}/`` (``Depth: 0``) -> confirms
  the token is valid; used to validate the config flow input.
- ``PROPFIND /remote.php/dav/photospublic/{token}/`` (``Depth: 1``) -> a
  WebDAV multistatus listing the album's files (name, size, content-type,
  last-modified, ``oc:fileid``).
- ``GET /remote.php/dav/photospublic/{token}/{filename}`` -> the real
  original file bytes (no proxy/re-encode), used both as the "original"
  quality display URL and for background EXIF enrichment regardless of the
  configured display quality.
- ``GET /index.php/apps/photos/api/v1/publicPreview/{fileId}?token=...&x=..&y=..``
  -> a resized preview JPEG, used as the "preview" (default) display quality.
"""
from __future__ import annotations

import email.utils
import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

import async_timeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_TIMEOUT = 30

# Matches a pasted public album link in either the "pretty URL" form or the
# ``/index.php/...`` form, with Nextcloud installed at the domain root or
# under a subdirectory. Examples:
#   https://cloud.example.com/apps/photos/public/AbC123
#   https://cloud.example.com/index.php/apps/photos/public/AbC123/
#   https://example.com/nextcloud/apps/photos/public/AbC123?something
_SHARE_LINK_RE = re.compile(
    r"^(?P<base>https?://[^\s]+?)/(?:index\.php/)?apps/photos/public/"
    r"(?P<token>[A-Za-z0-9]+)/?(?:[?#].*)?$"
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
    "<d:prop>"
    "<d:getcontenttype/>"
    "<d:getcontentlength/>"
    "<d:getlastmodified/>"
    "<d:resourcetype/>"
    "<oc:fileid/>"
    "</d:prop>"
    "</d:propfind>"
).encode("utf-8")

_DAV_NS = "DAV:"
_OC_NS = "http://owncloud.org/ns"


def normalize_base_url(url: str) -> str:
    """Strip trailing slashes from a base URL."""
    return (url or "").strip().rstrip("/")


def parse_share_link(url: str) -> tuple[str, str] | None:
    """Extract ``(base_url, token)`` from a pasted public album link.

    Returns ``None`` if the link doesn't look like a Nextcloud Photos public
    album share.
    """
    if not url:
        return None
    match = _SHARE_LINK_RE.match(url.strip())
    if not match:
        return None
    return normalize_base_url(match.group("base")), match.group("token")


def dav_root(base_url: str, token: str) -> str:
    """Return the ``photospublic`` WebDAV collection URL for this album."""
    return f"{normalize_base_url(base_url)}/remote.php/dav/photospublic/{token}/"


def build_image_url(base_url: str, token: str, filename: str) -> str:
    """Build the "original" quality URL: a direct WebDAV GET of the real file."""
    return dav_root(base_url, token) + quote(filename)


def build_preview_url(
    base_url: str, token: str, file_id: str, px: int = 1024
) -> str:
    """Build the "preview" quality URL via the Photos app's publicPreview API."""
    base = normalize_base_url(base_url)
    return (
        f"{base}/index.php/apps/photos/api/v1/publicPreview/{file_id}"
        f"?token={token}&x={px}&y={px}"
    )


def _looks_like_image(content_type: str | None, filename: str) -> bool:
    if content_type:
        return content_type.split(";", 1)[0].strip().lower().startswith("image/")
    ext = filename[filename.rfind(".") :].lower() if "." in filename else ""
    return ext in _IMAGE_EXTS


def _mtime_to_epoch_ms(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None
    try:
        return int(dt.timestamp() * 1000)
    except (OverflowError, OSError, ValueError):
        return None


def parse_propfind_response(xml_text: str, root_url: str) -> list[dict[str, Any]]:
    """Parse a WebDAV multistatus response into a list of image file dicts.

    Skips the self-referencing root entry, any collection (folder) entries,
    and any entry that doesn't look like an image. Returned dicts carry
    ``filename``, ``content_type``, ``size``, ``mtime_ms`` and ``file_id``
    (any of the latter three may be ``None`` when the server omitted them).
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    root_path = urlparse(root_url).path.rstrip("/")

    out: list[dict[str, Any]] = []
    for response in root.findall(f"{{{_DAV_NS}}}response"):
        href_el = response.find(f"{{{_DAV_NS}}}href")
        if href_el is None or not href_el.text:
            continue
        href_path = unquote(href_el.text).rstrip("/")
        if href_path == root_path:
            continue  # the album folder itself

        prop = None
        for propstat in response.findall(f"{{{_DAV_NS}}}propstat"):
            status = propstat.findtext(f"{{{_DAV_NS}}}status") or ""
            if " 200 " in status:
                prop = propstat.find(f"{{{_DAV_NS}}}prop")
                break
        if prop is None:
            continue

        resourcetype = prop.find(f"{{{_DAV_NS}}}resourcetype")
        if resourcetype is not None and resourcetype.find(
            f"{{{_DAV_NS}}}collection"
        ) is not None:
            continue  # folder, not a photo

        filename = unquote(href_path.rsplit("/", 1)[-1])
        if not filename:
            continue

        content_type = prop.findtext(f"{{{_DAV_NS}}}getcontenttype")
        if not _looks_like_image(content_type, filename):
            continue

        size_raw = prop.findtext(f"{{{_DAV_NS}}}getcontentlength")
        try:
            size = int(size_raw) if size_raw is not None else None
        except ValueError:
            size = None

        out.append(
            {
                "filename": filename,
                "content_type": content_type,
                "size": size,
                "mtime_ms": _mtime_to_epoch_ms(
                    prop.findtext(f"{{{_DAV_NS}}}getlastmodified")
                ),
                "file_id": prop.findtext(f"{{{_OC_NS}}}fileid"),
            }
        )

    return out


class NextcloudClient:
    """Thin async wrapper over the ``photospublic`` WebDAV collection."""

    def __init__(self, hass: Any, base_url: str, token: str) -> None:
        self.hass = hass
        self.base_url = normalize_base_url(base_url)
        self.token = token

    @property
    def dav_root(self) -> str:
        return dav_root(self.base_url, self.token)

    async def _propfind(self, depth: int) -> str:
        session = async_get_clientsession(self.hass)
        headers = {"Depth": str(depth), "Content-Type": "application/xml"}
        async with async_timeout.timeout(_TIMEOUT):
            async with session.request(
                "PROPFIND", self.dav_root, data=_PROPFIND_BODY, headers=headers
            ) as resp:
                resp.raise_for_status()
                return await resp.text()

    async def async_validate(self) -> None:
        """Confirm the token/URL work. Raises on any failure."""
        await self._propfind(depth=0)

    async def async_list_photos(self) -> list[dict[str, Any]]:
        """Return the album's image files."""
        xml_text = await self._propfind(depth=1)
        return parse_propfind_response(xml_text, self.dav_root)
