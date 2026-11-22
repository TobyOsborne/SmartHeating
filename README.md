# Smart Heating

A Home Assistant integration that heats by zones and a priority list, so the boiler
has enough open radiator to burn efficiently and spare heat goes where it is useful.
The design is in the "Smart Heating – Proposal & Structure" doc.

## Status

**0.2.0 – dry run.** Installs from the Marketplace as a custom repository and starts in
**dry run**: it reads the thermometers, boiler and flow temperature, decides every minute,
and shows what it would do, but sends nothing to the boiler or the TRVs. Turn off
`switch.smart_heating_dry_run` to let it take control.

| Entity | Shows |
| --- | --- |
| `select.smart_heating_profile` | Running profile |
| `select.smart_heating_mode` | Auto, Manual, Off or Frost |
| `switch.smart_heating_dry_run` | On = decide and display only |
| `binary_sensor.smart_heating_boiler` | Whether the boiler should be on |
| `binary_sensor.smart_heating_<radiator>_open` | Each radiator, with its reason and modelled kW |
| `sensor.smart_heating_<zone>` | heating / satisfied / waiting / idle / window / frost / offline / boost |
| `sensor.smart_heating_open_capacity` | Open radiator kW against the boiler minimum |
| `sensor.smart_heating_reason` | Why the boiler is on or off, plus recent notifications |

Services: `smart_heating.set_profile`, `set_mode`, `boost`, `cancel_boost`, `resume`, `recalculate`.

Radiators, zones and profiles start from `seed.py` and are stored in `.storage/smart_heating`.
The setup panel for editing them comes in a later release.

| File | What it holds |
| --- | --- |
| `model.py`, `controller.py` | Data model and the control algorithm (no Home Assistant imports) |
| `seed.py`, `store.py` | Starting configuration; conversion to and from storage |
| `coordinator.py` | Runs the controller in HA, applies decisions, checks TRVs took them |
| `config_flow.py`, entity platforms | Setup form and entities |

## Run the tests

```bash
python3 -m pip install pytest-homeassistant-custom-component
python3 -m pytest -q
```
