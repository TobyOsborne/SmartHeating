"""WebSocket API used by the Heating panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, SIGNAL_UPDATE
from .model import Mode
from .controller import radiator_output_w
from .store import config_from_dict, config_to_dict


def _entry(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0] if entries else None


def _coordinator(hass: HomeAssistant):
    entry = _entry(hass)
    return entry.runtime_data if entry else None


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_state)
    websocket_api.async_register_command(hass, ws_save_config)
    websocket_api.async_register_command(hass, ws_set)


def build_state(hass: HomeAssistant, coord) -> dict[str, Any]:
    d = coord.decision
    cfg = coord.config
    s = cfg.settings
    flow = coord._flow_temp()
    zones = {}
    for name, zone in cfg.zones.items():
        r = coord._reading(zone.thermometer)
        f = coord.filters.get(zone.thermometer)
        zones[name] = {"temperature": r.temp, "state": d.zone_states.get(name, "idle") if d else None,
                       "boost": any(o.zone == name for o in coord.runtime.overrides),
                       "ignored": f.rejected if f else 0, "last_ignored": f.last_rejected if f else None}
    radiators = {}
    for name, rad in cfg.radiators.items():
        room = next((zones[z]["temperature"] for z, zone in cfg.zones.items()
                     if name in zone.radiators and zones[z]["temperature"] is not None), s.default_room_temp)
        trv = hass.states.get(rad.trv)
        radiators[name] = {
            "open": bool(d and name in d.open),
            "reason": d.radiator_reasons.get(name) if d else None,
            "kw": round(radiator_output_w(rad.rated_w, flow, s.drop, room, rad.exponent) / 1000, 2),
            "trv_setpoint": trv.attributes.get("temperature") if trv else None,
            "trv_available": trv is not None and trv.state not in ("unavailable", "unknown"),
            "faulty": name in coord.faulty,
            "held": bool(d and name in d.held),
        }
    boiler = hass.states.get(coord.boiler)
    return {
        "config": config_to_dict(cfg),
        "runtime": {
            "mode": coord.runtime.mode.value,
            "profile": coord.runtime.profile,
            "dry_run": coord.runtime.dry_run,
            "overrides": [{"zone": o.zone, "target": o.target, "position": o.position,
                           "expires_at": o.expires_at} for o in coord.runtime.overrides],
        },
        "decision": {
            "boiler_on": d.boiler_on if d else None,
            "reason": d.reason if d else None,
            "capacity_kw": round(d.capacity_w / 1000, 2) if d else None,
            "needed_kw": round(d.needed_w / 1000, 2) if d else None,
        },
        "boiler_actual": boiler.state if boiler else None,
        "flow_temp": flow,
        "outdoor": coord._reading(s.outdoor_sensor).temp,
        "zones": zones,
        "radiators": radiators,
        "learning": coord.learning_summary(),
        "notifications": coord.recent_notifications[:10],
        "entities": {
            "climate": sorted(e for e in hass.states.async_entity_ids("climate")),
            "temperature": sorted(st.entity_id for st in hass.states.async_all("sensor")
                                  if st.attributes.get("device_class") == "temperature"),
        },
    }


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/state"})
@callback
def ws_state(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coord = _coordinator(hass)
    if coord is None:
        connection.send_error(msg["id"], "not_loaded", "Smart Heating is not set up")
        return
    connection.send_result(msg["id"], build_state(hass, coord))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/save_config", vol.Required("config"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    coord = _coordinator(hass)
    if coord is None:
        connection.send_error(msg["id"], "not_loaded", "Smart Heating is not set up")
        return
    try:
        config = config_from_dict(msg["config"])
    except (KeyError, TypeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_config", str(err))
        return
    before = (set(coord.config.zones), set(coord.config.radiators))
    await coord.async_reload_config(config)
    connection.send_result(msg["id"], build_state(hass, coord))
    if before != (set(config.zones), set(config.radiators)):
        # Zones or radiators were added/removed: rebuild their entities
        hass.config_entries.async_schedule_reload(_entry(hass).entry_id)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/set",
    vol.Optional("mode"): vol.In([m.value for m in Mode]),
    vol.Optional("profile"): str,
    vol.Optional("dry_run"): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Change mode, profile or dry run from the panel."""
    coord = _coordinator(hass)
    if coord is None:
        connection.send_error(msg["id"], "not_loaded", "Smart Heating is not set up")
        return
    try:
        if "profile" in msg:
            await coord.async_set_profile(msg["profile"])
        if "mode" in msg:
            await coord.async_set_mode(Mode(msg["mode"]))
        if "dry_run" in msg:
            await coord.async_set_dry_run(msg["dry_run"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    async_dispatcher_send(hass, SIGNAL_UPDATE)
    connection.send_result(msg["id"], build_state(hass, coord))
