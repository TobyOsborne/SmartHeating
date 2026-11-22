# Smart Heating

A Home Assistant integration that heats by zones and a priority list, so the boiler
has enough open radiator to burn efficiently and spare heat goes where it is useful.
The design is in the "Smart Heating – Proposal & Structure" doc.

## Status

**0.1.0 – controller and tests** (roll-out step 3). The algorithm runs as plain Python
with no Home Assistant imports:

| File | What it holds |
| --- | --- |
| `custom_components/smart_heating/model.py` | Radiators, zones, profiles, overrides, settings, tick inputs and decisions |
| `custom_components/smart_heating/controller.py` | `tick()`, radiator output maths, overrides, window detection |
| `custom_components/smart_heating/seed.py` | Starting radiators, zones and profiles for the house |
| `tests/test_controller.py` | Scenario tests, including the proposal's worked Office example |

Next: config flow, store, coordinator and actuator, running in **dry run** (decisions shown, nothing sent).

## Run the tests

```bash
python3 -m pip install pytest
python3 -m pytest -q
```
