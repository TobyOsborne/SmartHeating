"""Home Assistant side: setup, entities, dry run, going live, services."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.smart_heating.const import DOMAIN
from custom_components.smart_heating.seed import seed_config


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield

ENTRY_DATA = {
    "boiler_switch": "switch.boiler",
    "flow_temperature_entity": "input_number.heating_flow_temperature",
    "notify_service": "notify.mobile_app_test",
}


def _world(hass: HomeAssistant, office: float = 17.0) -> None:
    cfg = seed_config()
    temps = {"Office": office, "Landing": 14.0, "Main Bedroom": 13.0, "Living Room": 14.0,
             "Kitchen": 13.0, "Bathroom": 13.0, "Guest Bedroom": 13.0}
    for zone in cfg.zones.values():
        hass.states.async_set(zone.thermometer, str(temps[zone.name]), {"unit_of_measurement": "°C"})
    for rad in cfg.radiators.values():
        hass.states.async_set(rad.trv, "heat", {"temperature": 5})
    hass.states.async_set("switch.boiler", "off")
    hass.states.async_set("input_number.heating_flow_temperature", "60")


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_starts_in_dry_run_and_sends_nothing(hass: HomeAssistant) -> None:
    _world(hass)
    climate_calls = async_mock_service(hass, "climate", "set_temperature")
    await _setup(hass)
    # Mock after setup: loading our switch platform sets up HA's switch domain and its services
    switch_calls = async_mock_service(hass, "switch", "turn_on")

    assert hass.states.get("switch.smart_heating_dry_run").state == "on"
    assert hass.states.get("select.smart_heating_profile").state == "Office"
    assert hass.states.get("select.smart_heating_mode").state == "Auto"
    # The controller wants heat and the Office + Stairs radiators...
    assert hass.states.get("binary_sensor.smart_heating_boiler").state == "on"
    assert hass.states.get("binary_sensor.smart_heating_office_open").state == "on"
    assert hass.states.get("binary_sensor.smart_heating_living_room_stairs_open").state == "on"
    assert hass.states.get("binary_sensor.smart_heating_kitchen_open").state == "off"
    assert hass.states.get("sensor.smart_heating_reason").state.startswith("[dry run] Office 17.0°")
    assert hass.states.get("sensor.smart_heating_office").state == "heating"
    assert float(hass.states.get("sensor.smart_heating_open_capacity").state) > 1.0
    # ...but nothing was sent.
    assert climate_calls == [] and switch_calls == []


async def test_going_live_sends_boiler_and_trv_commands(hass: HomeAssistant, freezer) -> None:
    _world(hass)
    climate_calls = async_mock_service(hass, "climate", "set_temperature")
    await _setup(hass)
    # Mock after setup: loading our switch platform sets up HA's switch domain and its services
    switch_calls = async_mock_service(hass, "switch", "turn_on")

    await hass.services.async_call(DOMAIN, "recalculate", {}, blocking=True)
    assert climate_calls == [] and switch_calls == []
    coord = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await coord.async_set_dry_run(False)
    await hass.async_block_till_done()

    # The boiler switch changed state moments ago, so its 10-minute minimum off time holds it;
    # until it runs, only the primary zone's radiator opens.
    sent = {c.data["entity_id"]: c.data["temperature"] for c in climate_calls}
    assert sent == {"climate.trv_office": 30.0}
    assert switch_calls == []
    # ...until that has passed.
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in switch_calls] == ["switch.boiler"]
    sent = {c.data["entity_id"]: c.data["temperature"] for c in climate_calls}
    assert sent == {"climate.trv_office": 30.0, "climate.trv_living_room_stairs": 30.0}


async def test_services_change_profile_mode_and_boost(hass: HomeAssistant) -> None:
    _world(hass, office=19.0)
    await _setup(hass)

    await hass.services.async_call(DOMAIN, "set_profile", {"profile": "Kitchen"}, blocking=True)
    assert hass.states.get("select.smart_heating_profile").state == "Kitchen"
    assert hass.states.get("binary_sensor.smart_heating_boiler").state == "on"  # kitchen at 13°

    await hass.services.async_call(DOMAIN, "boost", {"zone": "Office", "target": 21, "position": 0,
                                                     "minutes": 20}, blocking=True)
    assert hass.states.get("sensor.smart_heating_office").state == "boost"
    assert hass.states.get("binary_sensor.smart_heating_office_open").state == "on"

    await hass.services.async_call(DOMAIN, "cancel_boost", {}, blocking=True)
    assert hass.states.get("sensor.smart_heating_office").state != "boost"

    await hass.services.async_call(DOMAIN, "set_mode", {"mode": "off"}, blocking=True)
    assert hass.states.get("select.smart_heating_mode").state == "Off"
    assert hass.states.get("binary_sensor.smart_heating_boiler").state == "off"


async def test_config_survives_reload(hass: HomeAssistant) -> None:
    _world(hass)
    entry = await _setup(hass)
    await hass.services.async_call(DOMAIN, "set_profile", {"profile": "Living Room"}, blocking=True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.smart_heating_profile").state == "Living Room"
    assert hass.states.get("switch.smart_heating_dry_run").state == "on"


async def test_config_flow_creates_single_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] == "create_entry"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "abort"


async def test_no_offline_alert_while_sensors_restore_after_restart(hass: HomeAssistant, freezer) -> None:
    _world(hass)
    cfg = seed_config()
    hass.states.async_set(cfg.zones["Office"].thermometer, "unavailable")
    entry = await _setup(hass)
    coord = entry.runtime_data
    assert hass.states.get("sensor.smart_heating_office").state == "offline"
    assert coord.recent_notifications == []  # restoring, not broken
    # Still missing well after start-up: now it alerts
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [n["kind"] for n in coord.recent_notifications] == ["sensor_offline"]


async def test_zero_glitch_does_not_trigger_frost_or_window(hass: HomeAssistant) -> None:
    _world(hass, office=19.0)  # Office profile satisfied: boiler off
    entry = await _setup(hass)
    bedroom = seed_config().zones["Main Bedroom"].thermometer
    hass.states.async_set(bedroom, "0", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    await entry.runtime_data.async_tick()
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.smart_heating_boiler").state == "off"
    zone = hass.states.get("sensor.smart_heating_main_bedroom")
    assert zone.state not in ("frost", "window")
    assert zone.attributes["temperature"] == 13.0
    assert zone.attributes["ignored_readings"] == 1


async def test_energy_sensors_track_boiler_time_and_meter(hass: HomeAssistant, freezer) -> None:
    _world(hass)
    hass.states.async_set("sensor.outdoor_temperature", "7")
    hass.states.async_set("sensor.house_dcc_sourced_gas_last_full_day", "21.8")
    hass.states.async_set("sensor.house_dcc_sourced_gas_cost_last_full_day", "1.68")
    hass.states.async_set("sensor.house_dcc_sourced_gas_data_until", "2026-11-22T17:00:00+00:00")
    hass.states.async_set("sensor.house_dcc_sourced_gas_unit_rate", "0.077")
    hass.states.async_set("sensor.house_dcc_sourced_gas_standing_charge", "0.1683")
    entry = await _setup(hass)
    coord = entry.runtime_data
    hass.states.async_set("switch.boiler", "on")
    for _ in range(30):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    hours = float(hass.states.get("sensor.smart_heating_boiler_hours_today").state)
    assert 0.45 < hours < 0.55
    cost = float(hass.states.get("sensor.smart_heating_heating_cost_today").state)
    assert cost == round((3.0 + 7.0 * hours) * 0.077 + 0.1683, 2) or abs(cost - ((3 + 7 * hours) * 0.077 + 0.1683)) < 0.02
    assert coord.energy.days["2026-11-21"].gas_kwh == 21.8
    assert hass.states.get("sensor.smart_heating_heating_cost_next_7_days").attributes["status"] == "learning"
    assert hass.states.get("sensor.smart_heating_suggested_flow_temperature").attributes["status"] == "learning"


async def test_trv_changed_by_hand_is_put_back(hass: HomeAssistant, freezer) -> None:
    _world(hass, office=19.0)  # Office warm: boiler off, every valve should be closed (5°)
    climate_calls = async_mock_service(hass, "climate", "set_temperature")
    entry = await _setup(hass)
    coord = entry.runtime_data
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    await coord.async_set_dry_run(False)
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    climate_calls.clear()

    # Someone turns the Main Bedroom TRV up to 25°
    hass.states.async_set("climate.trv_bedroom", "heat", {"temperature": 25})
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    bedroom = [c.data["temperature"] for c in climate_calls if c.data["entity_id"] == "climate.trv_bedroom"]
    assert bedroom == [5]
    assert [n["kind"] for n in coord.recent_notifications if n["kind"] == "trv_drift"] == ["trv_drift"]

    # Still wrong next minute: it's being verified, not spammed
    climate_calls.clear()
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c for c in climate_calls if c.data["entity_id"] == "climate.trv_bedroom"] == []


async def test_trv_drift_correction_can_be_turned_off(hass: HomeAssistant, freezer) -> None:
    _world(hass, office=19.0)
    climate_calls = async_mock_service(hass, "climate", "set_temperature")
    entry = await _setup(hass)
    coord = entry.runtime_data
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    coord.config.settings.correct_trv_drift = False
    await coord.async_set_dry_run(False)
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    climate_calls.clear()
    hass.states.async_set("climate.trv_bedroom", "heat", {"temperature": 25})
    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c for c in climate_calls if c.data["entity_id"] == "climate.trv_bedroom"] == []
