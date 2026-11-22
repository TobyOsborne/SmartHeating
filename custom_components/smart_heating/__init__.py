"""Smart Heating: zone and priority-list heating control for Home Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BOILER,
    CONF_FLOW_SOURCE,
    CONF_NOTIFY,
    DEFAULT_BOILER,
    DEFAULT_FLOW_SOURCE,
    DEFAULT_NOTIFY,
    DOMAIN,
    PLATFORMS,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import Runtime, SmartHeatingCoordinator
from .model import Mode
from .learning import ZoneLearning, seed_from_pi
from .seed import PI_COOLING, seed_config
from .store import config_from_dict
from . import panel, websocket

VERSION = "0.4.0"

_LOGGER = logging.getLogger(__name__)

type SmartHeatingConfigEntry = ConfigEntry[SmartHeatingCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SmartHeatingConfigEntry) -> bool:
    store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    data = await store.async_load() or {}

    if "config" in data:
        try:
            config = config_from_dict(data["config"])
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.error("Stored heating configuration is invalid (%s); starting from the seed", err)
            config = seed_config()
    else:
        config = seed_config()

    raw = data.get("runtime", {})
    runtime = Runtime(
        mode=Mode(raw.get("mode", Mode.AUTO.value)),
        profile=raw.get("profile", next(iter(config.profiles))),
        dry_run=raw.get("dry_run", True),  # always start new installs in dry run
    )

    if "learning" in data:
        learning = {name: ZoneLearning.from_dict(v) for name, v in data["learning"].items()}
    else:
        learning = {name: seed_from_pi(raw) for name, raw in PI_COOLING.items() if name in config.zones}

    opts = {**entry.data, **entry.options}
    coordinator = SmartHeatingCoordinator(
        hass, store, config, runtime,
        boiler=opts.get(CONF_BOILER, DEFAULT_BOILER),
        flow_source=opts.get(CONF_FLOW_SOURCE, DEFAULT_FLOW_SOURCE),
        notify=opts.get(CONF_NOTIFY, DEFAULT_NOTIFY),
        learning=learning,
    )
    entry.runtime_data = coordinator
    if "config" not in data or "learning" not in data:
        await coordinator.async_save()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()
    _register_services(hass)
    if not hass.data.get(f"{DOMAIN}_ws"):
        websocket.async_register(hass)
        hass.data[f"{DOMAIN}_ws"] = True
    await panel.async_register_panel(hass, VERSION)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: SmartHeatingConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SmartHeatingConfigEntry) -> bool:
    await entry.runtime_data.async_stop()
    panel.async_remove_panel(hass)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _coordinator(hass: HomeAssistant) -> SmartHeatingCoordinator:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Smart Heating is not set up")
    return entries[0].runtime_data


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "set_profile"):
        return

    async def set_profile(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        try:
            await coord.async_set_profile(call.data["profile"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def set_mode(call: ServiceCall) -> None:
        await _coordinator(hass).async_set_mode(Mode(call.data["mode"]))

    async def boost(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        try:
            await coord.async_boost(call.data["zone"], call.data.get("target"),
                                    call.data.get("position"), call.data.get("minutes"))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def cancel_boost(call: ServiceCall) -> None:
        await _coordinator(hass).async_cancel_boost(call.data.get("zone"))

    async def resume(call: ServiceCall) -> None:
        coord = _coordinator(hass)
        await coord.async_cancel_boost(None)
        # Profile rules are ordinary automations; they can listen for this event.
        hass.bus.async_fire(f"{DOMAIN}_resume")

    async def recalculate(call: ServiceCall) -> None:
        await _coordinator(hass).async_tick()

    hass.services.async_register(DOMAIN, "set_profile", set_profile,
                                 schema=vol.Schema({vol.Required("profile"): cv.string}))
    hass.services.async_register(DOMAIN, "set_mode", set_mode,
                                 schema=vol.Schema({vol.Required("mode"): vol.In([m.value for m in Mode])}))
    hass.services.async_register(DOMAIN, "boost", boost, schema=vol.Schema({
        vol.Required("zone"): cv.string,
        vol.Optional("target"): vol.Coerce(float),
        vol.Optional("position"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("minutes"): vol.All(vol.Coerce(float), vol.Range(min=1)),
    }))
    hass.services.async_register(DOMAIN, "cancel_boost", cancel_boost,
                                 schema=vol.Schema({vol.Optional("zone"): cv.string}))
    hass.services.async_register(DOMAIN, "resume", resume)
    hass.services.async_register(DOMAIN, "recalculate", recalculate)
