from __future__ import annotations

import json
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_PROVIDER,
    CONF_ALBUM_NAME,
    CONF_ALBUM_URL,
    CONF_LOCAL_PATH,
    CONF_MEDIA_CONTENT_ID,
    CONF_RECURSIVE,
    CONF_REVERSE_GEOCODE,
    CONF_IMMICH_URL,
    CONF_IMMICH_API_KEY,
    CONF_IMMICH_SELECTION_TYPE,
    CONF_IMMICH_SELECTION_ID,
    CONF_IMMICH_IMAGE_SIZE,
    CONF_IMMICH_FILTER,
    DEFAULT_IMMICH_IMAGE_SIZE,
    IMMICH_IMAGE_SIZE_OPTIONS,
    IMMICH_SELECTION_ALBUM,
    IMMICH_SELECTION_PERSON,
    IMMICH_SELECTION_PEOPLE,
    IMMICH_SELECTION_FAVORITES,
    IMMICH_SELECTION_ALL,
    IMMICH_SELECTION_RANDOM,
    IMMICH_SELECTION_SEARCH,
    DEFAULT_REVERSE_GEOCODE,
    PROVIDER_GOOGLE_SHARED,
    PROVIDER_LOCAL_FOLDER,
    PROVIDER_MEDIA_SOURCE,
    PROVIDER_IMMICH,
    DEFAULT_RECURSIVE,
)


def _normalize_local_path(hass, path: str) -> str:
    p = path.strip()
    if p.startswith("/local/"):
        p = "/config/www/" + p[len("/local/"):]
    elif p == "/local":
        p = "/config/www"
    elif p.startswith("local/"):
        p = "/config/www/" + p[len("local/"):]
    elif p.startswith("/media/local/"):
        p = "/media/" + p[len("/media/local/"):]
    elif p.startswith("media/local/"):
        p = "/media/" + p[len("media/local/"):]
    elif p.startswith("media/"):
        p = "/media/" + p[len("media/"):]
    elif p == "media":
        p = "/media"
    if not p.startswith("/"):
        p = hass.config.path(p)
    return p


ALBUM_URL_RE = re.compile(r"^https?://photos\.app\.goo\.gl/[^/]+/?$")


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._provider: str | None = None
        # Immich flow state carried between steps.
        self._immich_url: str | None = None
        self._immich_key: str | None = None
        self._immich_options: dict[str, tuple[str, str]] = {}
        # Named people (id -> name) for the multi-select "People" source.
        self._immich_people: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler.

        Only local-folder entries expose user-tunable options today (the
        reverse-geocode toggle); Google entries get a no-op handler so
        that the "Configure" button doesn't appear empty in the UI.

        Note: do NOT pass ``config_entry`` to the OptionsFlow constructor.
        Since Home Assistant 2024.12 the base class manages
        ``self.config_entry`` as a property and assigning to it in
        ``__init__`` raises (the symptom is a 500 when the user clicks
        Configure).
        """
        if config_entry.data.get(CONF_PROVIDER) == PROVIDER_LOCAL_FOLDER:
            return LocalFolderOptionsFlow()
        return _NoOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._provider = user_input[CONF_PROVIDER]
            if self._provider == PROVIDER_LOCAL_FOLDER:
                return await self.async_step_local_folder()
            if self._provider == PROVIDER_MEDIA_SOURCE:
                return await self.async_step_media_source()
            if self._provider == PROVIDER_IMMICH:
                return await self.async_step_immich()
            return await self.async_step_google_shared()

        schema = vol.Schema(
            {
                vol.Required(CONF_PROVIDER, default=PROVIDER_GOOGLE_SHARED): vol.In({
                    PROVIDER_GOOGLE_SHARED: "Google Photos",
                    PROVIDER_LOCAL_FOLDER: "Local Folder",
                    PROVIDER_IMMICH: "Immich (direct API, full metadata)",
                    PROVIDER_MEDIA_SOURCE: "Media Source (any source, no metadata)",
                })
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_google_shared(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_ALBUM_URL].strip()
            name = user_input[CONF_ALBUM_NAME].strip()

            if not ALBUM_URL_RE.match(url):
                errors[CONF_ALBUM_URL] = "invalid_album_url"
            else:
                await self.async_set_unique_id(f"{DOMAIN}:{PROVIDER_GOOGLE_SHARED}:{url}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_PROVIDER: PROVIDER_GOOGLE_SHARED,
                        CONF_ALBUM_URL: url,
                        CONF_ALBUM_NAME: name,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_ALBUM_NAME): str,
                vol.Required(CONF_ALBUM_URL): str,
            }
        )
        return self.async_show_form(step_id="google_shared", data_schema=schema, errors=errors)

    async def async_step_local_folder(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            path = _normalize_local_path(self.hass, user_input[CONF_LOCAL_PATH])
            name = user_input[CONF_ALBUM_NAME].strip()
            recursive = bool(user_input.get(CONF_RECURSIVE, DEFAULT_RECURSIVE))

            if not path:
                errors[CONF_LOCAL_PATH] = "invalid_path"
            else:
                await self.async_set_unique_id(f"{DOMAIN}:{PROVIDER_LOCAL_FOLDER}:{path}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_PROVIDER: PROVIDER_LOCAL_FOLDER,
                        CONF_LOCAL_PATH: path,
                        CONF_RECURSIVE: recursive,
                        CONF_ALBUM_NAME: name,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_ALBUM_NAME): str,
                vol.Required(CONF_LOCAL_PATH): str,
                vol.Optional(CONF_RECURSIVE, default=DEFAULT_RECURSIVE): bool,
            }
        )
        return self.async_show_form(step_id="local_folder", data_schema=schema, errors=errors)

    async def async_step_media_source(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            content_id = user_input[CONF_MEDIA_CONTENT_ID].strip()
            name = user_input[CONF_ALBUM_NAME].strip()

            if not content_id.startswith("media-source://"):
                errors[CONF_MEDIA_CONTENT_ID] = "invalid_media_source"
            else:
                await self.async_set_unique_id(
                    f"{DOMAIN}:{PROVIDER_MEDIA_SOURCE}:{content_id}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_PROVIDER: PROVIDER_MEDIA_SOURCE,
                        CONF_MEDIA_CONTENT_ID: content_id,
                        CONF_ALBUM_NAME: name,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_ALBUM_NAME): str,
                vol.Required(CONF_MEDIA_CONTENT_ID): str,
            }
        )
        return self.async_show_form(
            step_id="media_source", data_schema=schema, errors=errors
        )

    async def async_step_immich(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect the Immich URL + API key and validate them."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_IMMICH_URL].strip()
            key = user_input[CONF_IMMICH_API_KEY].strip()
            from . import immich as immich_api

            client = immich_api.ImmichClient(self.hass, url, key)
            try:
                await client.async_validate()
                albums = await client.async_list_albums()
                people = await client.async_list_people()
            except Exception:  # noqa: BLE001 - any failure means bad URL/key
                errors["base"] = "immich_cannot_connect"
            else:
                self._immich_url = client.base_url
                self._immich_key = key
                # Map a display label -> (selection_type, id). Fixed sources
                # first, then the user's albums and named people.
                options: dict[str, tuple[str, str | None]] = {
                    "All photos (recent)": (IMMICH_SELECTION_ALL, None),
                    "Favorites": (IMMICH_SELECTION_FAVORITES, None),
                    "Random": (IMMICH_SELECTION_RANDOM, None),
                    "Custom search (JSON filter)": (IMMICH_SELECTION_SEARCH, None),
                }
                for a in albums:
                    if a.get("id"):
                        label = f"Album: {a.get('albumName') or a['id']}"
                        options[label] = (IMMICH_SELECTION_ALBUM, a["id"])
                named_people = [
                    p
                    for p in people
                    if p.get("id") and (p.get("name") or "").strip()
                ]
                if len(named_people) >= 2:
                    options["People (any of - pick below)"] = (
                        IMMICH_SELECTION_PEOPLE,
                        None,
                    )
                for p in named_people:
                    label = f"Person: {p['name']}"
                    options[label] = (IMMICH_SELECTION_PERSON, p["id"])
                self._immich_options = options
                # Named people for the multi-select "People (any of)" source.
                self._immich_people = {
                    p["id"]: p["name"] for p in named_people
                }
                return await self.async_step_immich_select()

        schema = vol.Schema(
            {
                vol.Required(CONF_IMMICH_URL): str,
                vol.Required(CONF_IMMICH_API_KEY): str,
            }
        )
        return self.async_show_form(
            step_id="immich", data_schema=schema, errors=errors
        )

    async def async_step_immich_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick an album or person and finish the Immich entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            label = user_input["selection"]
            name = user_input[CONF_ALBUM_NAME].strip()
            size = user_input.get(CONF_IMMICH_IMAGE_SIZE, DEFAULT_IMMICH_IMAGE_SIZE)
            raw_filter = (user_input.get(CONF_IMMICH_FILTER) or "").strip()
            sel = self._immich_options.get(label)
            if not sel:
                errors["base"] = "immich_no_content"
            else:
                sel_type, sel_id = sel
                if sel_type == IMMICH_SELECTION_SEARCH:
                    if not raw_filter:
                        errors[CONF_IMMICH_FILTER] = "immich_filter_required"
                    else:
                        try:
                            parsed = json.loads(raw_filter)
                            if not isinstance(parsed, dict):
                                raise ValueError
                        except ValueError:
                            errors[CONF_IMMICH_FILTER] = "immich_filter_invalid"
                if sel_type == IMMICH_SELECTION_PEOPLE:
                    chosen = [
                        p for p in user_input.get("people", []) if p
                    ]
                    if not chosen:
                        errors["people"] = "immich_people_required"
                    else:
                        sel_id = ",".join(chosen)
                if not errors:
                    unique = (
                        f"{DOMAIN}:{PROVIDER_IMMICH}:{self._immich_url}:"
                        f"{sel_type}:{sel_id or raw_filter or name}"
                    )
                    await self.async_set_unique_id(unique)
                    self._abort_if_unique_id_configured()
                    data = {
                        CONF_PROVIDER: PROVIDER_IMMICH,
                        CONF_IMMICH_URL: self._immich_url,
                        CONF_IMMICH_API_KEY: self._immich_key,
                        CONF_IMMICH_SELECTION_TYPE: sel_type,
                        CONF_IMMICH_SELECTION_ID: sel_id or "",
                        CONF_IMMICH_IMAGE_SIZE: size,
                        CONF_ALBUM_NAME: name,
                    }
                    if sel_type == IMMICH_SELECTION_SEARCH:
                        data[CONF_IMMICH_FILTER] = raw_filter
                    return self.async_create_entry(title=name, data=data)

        labels = list(self._immich_options.keys())
        fields: dict[Any, Any] = {
            vol.Required(CONF_ALBUM_NAME): str,
            vol.Required("selection"): vol.In(labels),
        }
        if self._immich_people:
            fields[vol.Optional("people")] = cv.multi_select(self._immich_people)
        fields[vol.Optional(CONF_IMMICH_FILTER)] = str
        fields[
            vol.Optional(CONF_IMMICH_IMAGE_SIZE, default=DEFAULT_IMMICH_IMAGE_SIZE)
        ] = vol.In(IMMICH_IMAGE_SIZE_OPTIONS)
        schema = vol.Schema(fields)
        return self.async_show_form(
            step_id="immich_select", data_schema=schema, errors=errors
        )


class LocalFolderOptionsFlow(config_entries.OptionsFlow):
    """Options for local-folder entries.

    Currently exposes a single toggle: ``reverse_geocode``. Users with
    privacy concerns about handing EXIF GPS coordinates to an external
    OSM endpoint can turn this off; the GPS coordinates remain available
    as ``latitude``/``longitude`` attributes regardless.

    ``self.config_entry`` is provided by ``OptionsFlow`` as a managed
    property (HA 2024.12+); we deliberately do NOT define ``__init__``
    or assign to it, since doing so raises in newer cores.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_REVERSE_GEOCODE, DEFAULT_REVERSE_GEOCODE
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_REVERSE_GEOCODE, default=bool(current)
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


class _NoOptionsFlow(config_entries.OptionsFlow):
    """Fallback options flow for providers that expose nothing tunable."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_create_entry(title="", data={})
