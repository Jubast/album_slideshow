# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant custom integration (HACS component) that exposes a `camera` entity rendering a slideshow from Google Photos shared albums, Immich, PhotoPrism, local/NAS folders, or any HA Media Source. All source lives under `custom_components/album_slideshow/`. It ships its own Lovelace card (`www/album-slideshow-card.js`) that does browser-side cross-fade transitions.

## Commands

Run tests (no Home Assistant install required — `tests/conftest.py` stubs the `homeassistant` package):
```bash
pip install -r tests/requirements.txt
pytest tests/ -q
```
Run a single test file or test:
```bash
pytest tests/test_playlist.py -q
pytest tests/test_playlist.py::test_name -q
```

Syntax-check a single file (used in CI-adjacent checks, no linter is configured):
```bash
python3 -c "import ast; ast.parse(open('custom_components/album_slideshow/camera.py').read())"
```

Run a live Home Assistant instance against this integration (devcontainer/local dev loop, from repo root):
```bash
scripts/setup     # pip install -r requirements.txt
scripts/develop   # creates ./config, symlinks PYTHONPATH to custom_components, runs `hass --config ./config --debug`
```

There is no build step and no JS bundler — `www/album-slideshow-card.js` is hand-written vanilla JS served directly.

## CI gates (`.github/workflows/`)

- `tests.yaml` — pytest on 3.11/3.12, plus a `version-sync` job that **fails the build if `manifest.json`'s `version` doesn't match the `VERSION` constant at the top of `www/album-slideshow-card.js`**. Bump both together on every release.
- `validate.yaml` — runs `hassfest` (HA's manifest/structure validator).
- `hacs.yaml` — HACS repository validation.
- `release.yaml` — on `v*` tag push, asserts the tag matches `manifest.json` version, then zips `custom_components/album_slideshow/` and attaches it to the GitHub release.

## Architecture

### Request flow
`AlbumCoordinator` (coordinator.py, a `DataUpdateCoordinator`) polls/scans the configured source on `refresh_hours` and produces a list of `MediaItem` (url, dimensions, `captured_at`/`uploaded_at` epoch-ms, GPS, `location`, `description`, `source_id`). `AlbumSlideshowCamera` (camera.py) owns actual slide selection and rendering: it applies `playlist.py`'s pure `order_items`/`filter_items` helpers to the coordinator's items, downloads/caches the winning image(s), and hands them to `image_processing.py` to compose the final JPEG/PNG frame (fill mode, orientation pairing, aspect ratio, divider). The card (`www/album-slideshow-card.js`) polls the camera entity's still image plus its attributes (`frame_id`, `captured_at`, `location`, etc.) and does the actual cross-fade transition client-side — the Python side never renders a transition burst.

### Runtime-configurable settings are entities, not YAML
`SlideshowStore` (store.py) is a single in-memory dataclass (slide interval, fill mode, order mode, aspect ratio, date filter, pause state, etc.) shared by one `number`/`select`/`text`/`switch` entity per field (see `number.py`, `select.py`, `text.py`, `switch.py`). Each entity writes straight into the shared `store` and calls `store.notify()`, which fans out to listeners (primarily the camera) so changes take effect immediately with no HA restart and no coordinator reload. `config_flow.py` only handles one-time setup (provider choice, credentials, album selection) — everything a user tweaks routinely lives in the store instead.

### Providers
Each image source is a self-contained module producing `MediaItem`s for the coordinator, selected via `CONF_PROVIDER` (`const.py`):
- `google_scraper.py` — scrapes the public (undocumented) Google Photos shared-album web endpoints; no official API exists, so this is the most fragile provider and falls back to a 300-photo cap if Google changes the page format.
- `immich.py` / `photoprism.py` — direct authenticated REST clients (`ImmichClient`, `PhotoprismClient`) supporting album/person/favorites/search selections. Both APIs lack an "OR" query, so the coordinator queries each selected album/person separately and merges+dedupes results client-side (see `IMMICH_SELECTION_COMPOSITE` / `PHOTOPRISM_SELECTION_COMPOSITE` in const.py). Both keep the API key/password server-side only — images are fetched by HA and re-served, never exposing credentials or the origin server to the dashboard client.
- Local folder / NAS — coordinator.py walks the filesystem directly, reads EXIF (`_read_local_exif`) for capture date/GPS/description, and reverse-geocodes GPS via Nominatim in the background (`_nominatim_lookup`, disk-cached, ~100m rounding, opt-out via `CONF_REVERSE_GEOCODE`).
- Media Source — coordinator.py browses any `media-source://` id (Immich-via-HA, Jellyfin, local media) via HA's media_source integration; this path gets only URLs, so no EXIF/date/location metadata is available (this is the main reason to prefer the direct Immich/PhotoPrism providers when available).

Local-folder and Immich metadata enrichment (EXIF read / per-asset Immich detail calls) run as a background pass after the initial item list loads, progress-tracked via the diagnostic "Enrichment progress" sensor — don't assume `captured_at`/`location` are populated on the first refresh for those providers.

### Tests mirror this module boundary
Each `tests/test_*.py` targets one module in isolation using the stubs in `tests/conftest.py` (which fakes just enough of `homeassistant.*` to import the integration without a real HA install) — e.g. `test_playlist.py` for ordering/date-filter logic, `test_coordinator_merge.py` for composite dedup, `test_image_processing.py` for rendering, `test_camera_next_slide.py` for slide-advance behavior. When changing shared logic (playlist ordering, EXIF parsing, provider merge logic), check for an existing test module before adding a new one.
