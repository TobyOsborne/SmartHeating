"""Sidebar panel registration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "smart-heating"
STATIC_URL = f"/{DOMAIN}_static"
_FLAG = f"{DOMAIN}_panel_static"


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return  # tests and minimal installs have no frontend
    try:
        from homeassistant.components import panel_custom
        from homeassistant.components.http import StaticPathConfig

        if not hass.data.get(_FLAG):
            await hass.http.async_register_static_paths([
                StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False)
            ])
            hass.data[_FLAG] = True
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name="smart-heating-panel",
            frontend_url_path=PANEL_URL,
            module_url=f"{STATIC_URL}/panel.js?v={version}",
            sidebar_title="Heating",
            sidebar_icon="mdi:radiator",
            require_admin=False,
            config={},
        )
    except Exception:  # noqa: BLE001 - the panel is optional, heating must still run
        _LOGGER.exception("Could not register the Heating panel")


def async_remove_panel(hass: HomeAssistant) -> None:
    if "frontend" not in hass.config.components:
        return
    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
