"""WebSocket API used by the Heating panel."""

from __future__ import annotations

import copy

import pytest
from homeassistant.core import HomeAssistant

from tests.test_integration import _setup, _world


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def test_state_has_everything_the_panel_needs(hass: HomeAssistant, hass_ws_client) -> None:
    _world(hass)
    hass.states.async_set("sensor.outdoor_temperature", "8")
    await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "smart_heating/state"})
    msg = await ws.receive_json()
    assert msg["success"], msg
    r = msg["result"]
    assert r["runtime"]["profile"] == "Office" and r["runtime"]["dry_run"] is True
    assert r["decision"]["boiler_on"] is True
    assert r["zones"]["Office"] == {"temperature": 17.0, "state": "heating", "boost": False, "ignored": 0, "last_ignored": None}
    assert r["radiators"]["Office"]["open"] is True and r["radiators"]["Office"]["kw"] > 0.5
    assert {p["name"] for p in r["config"]["profiles"]} >= {"Office", "Living Room", "Whole House"}
    lr = r["learning"]["Living Room"]
    assert lr["confidence"] == 71 and lr["settling_temp"] is not None
    assert "climate.trv_office" in r["entities"]["climate"]


async def test_save_config_adds_profile_and_rejects_bad_ones(hass: HomeAssistant, hass_ws_client) -> None:
    _world(hass)
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "smart_heating/state"})
    cfg = (await ws.receive_json())["result"]["config"]

    good = copy.deepcopy(cfg)
    good["profiles"].append({"name": "Night", "primary_zone": "Living Room", "goal": 13.5,
                             "priority": [], "spare_heat": "primary_only"})
    await ws.send_json({"id": 2, "type": "smart_heating/save_config", "config": good})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert "Night" in entry.runtime_data.config.profiles
    await hass.async_block_till_done()
    assert "Night" in hass.states.get("select.smart_heating_profile").attributes["options"]

    bad = copy.deepcopy(cfg)
    bad["profiles"][0]["primary_zone"] = "Attic"
    await ws.send_json({"id": 3, "type": "smart_heating/save_config", "config": bad})
    msg = await ws.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "invalid_config"
    assert "Night" in entry.runtime_data.config.profiles  # unchanged


async def test_set_changes_profile_and_mode(hass: HomeAssistant, hass_ws_client) -> None:
    _world(hass)
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "smart_heating/set", "profile": "Living Room", "mode": "manual"})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert entry.runtime_data.runtime.profile == "Living Room"
    assert msg["result"]["runtime"]["mode"] == "manual"
    await ws.send_json({"id": 2, "type": "smart_heating/set", "profile": "Nope"})
    assert not (await ws.receive_json())["success"]


async def test_deleting_active_profile_falls_back(hass: HomeAssistant, hass_ws_client) -> None:
    _world(hass)
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "smart_heating/state"})
    cfg = (await ws.receive_json())["result"]["config"]
    cfg["profiles"] = [p for p in cfg["profiles"] if p["name"] != "Office"]
    await ws.send_json({"id": 2, "type": "smart_heating/save_config", "config": cfg})
    assert (await ws.receive_json())["success"]
    assert entry.runtime_data.runtime.profile in entry.runtime_data.config.profiles
