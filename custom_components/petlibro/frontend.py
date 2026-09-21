"""Register PETLIBRO frontend resources."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import (
    DOMAIN as LOVELACE_DOMAIN,
    MODE_STORAGE,
)
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.const import CONF_ID, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_FRONTEND_PATH = Path(__file__).parent / "frontend"
_MEAL_CARD_PATH = "/petlibro/frontend/petlibro-meal-card.js"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve and register the PETLIBRO meal card."""
    integration = await async_get_integration(hass, DOMAIN)
    version = integration.version or "0"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                _MEAL_CARD_PATH,
                str(_FRONTEND_PATH / "petlibro-meal-card.js"),
                cache_headers=True,
            )
        ]
    )

    lovelace_data = hass.data.get(LOVELACE_DOMAIN)
    if lovelace_data is None:
        _LOGGER.warning("Lovelace is unavailable; PETLIBRO meal card was not registered")
        return

    if isinstance(lovelace_data, dict):
        resource_mode = lovelace_data.get(
            "resource_mode", lovelace_data.get("mode")
        )
        resources = lovelace_data.get("resources")
    else:
        resource_mode = getattr(lovelace_data, "resource_mode", None)
        if resource_mode is None:
            resource_mode = getattr(lovelace_data, "mode", None)
        resources = getattr(lovelace_data, "resources", None)

    if resource_mode != MODE_STORAGE:
        _LOGGER.info(
            "Lovelace resources use YAML mode; add %s?v=%s as a module resource",
            _MEAL_CARD_PATH,
            version,
        )
        return

    if not isinstance(resources, ResourceStorageCollection):
        return

    await resources.async_get_info()
    resource_url = f"{_MEAL_CARD_PATH}?v={version}"
    existing = next(
        (
            item
            for item in resources.async_items()
            if urlsplit(item.get(CONF_URL, "")).path == _MEAL_CARD_PATH
        ),
        None,
    )
    if existing is None:
        await resources.async_create_item(
            {"res_type": "module", CONF_URL: resource_url}
        )
        return
    if existing.get(CONF_URL) != resource_url:
        await resources.async_update_item(
            existing[CONF_ID],
            {"res_type": "module", CONF_URL: resource_url},
        )
