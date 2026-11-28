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
| `select.smart_heating_mode` | Auto, Manual or Off (frost protection runs in all three) |
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

## Heating panel (0.3.0)

A **Heating** item appears in the sidebar:

- **Now** – profile, mode and dry run; what the boiler wants and why; open radiator output vs what the boiler needs; every room with its target and a Boost button; every radiator and why it's open or closed.
- **Profiles** – edit, add, duplicate or delete profiles, reorder the priority list, set targets, buffer rooms and spare-heat mode.
- **Zones / Radiators** – thermometers, which radiators heat each room, window detection, TRVs and rated outputs.
- **Learning** – per room: cooling half-life, free heat, settling temperature, warm-up rate and a confidence rating for each. Seeded from the old Pi's numbers.
- **Settings** – boiler minimums, hold times, frost protection and the outdoor sensor.

Edits are held until you press **Save**; invalid configurations are rejected and nothing changes.

## Profile rules (set up in Home Assistant, labelled `heating_rule`)

| Automation | Does |
| --- | --- |
| Heating rule - Night | `schedule.heating_night` (21:00–10:00) → Night, unless away; schedule ends → resume |
| Heating rule - Away | `binary_sensor.heating_away` (beyond `input_number.heating_away_distance` for 20 min, with `input_boolean.heating_away_mode` on) → Away; back → resume |
| Heating rule - Office in use | Socket (Office) above 50 W for 5 min, daytime, not away → Office; socket off or 15-min max below 50 W → resume |
| Heating rule - Resume | `smart_heating.resume` → `script.heating_resume_schedule` |

`script.heating_resume_schedule` picks Away, then Night, then Living Room.

## Room minimums (0.4.0)

Each zone can have a **minimum** (Zones tab). It applies in every profile: whenever the boiler is
running, rooms below their minimum open first, coldest first, and stop once they reach it (with the
zone's hysteresis). A minimum never starts the boiler on its own; frost protection does that.

## Thermometer glitch filter (0.4.1)

Room thermometer readings outside -10–45°, or jumping more than 3° from the last good reading, are
ignored and the last good value is kept. A jump that holds steady for 5 minutes is accepted (the
sensor really moved). A thermometer that only sends glitches goes offline after the usual 15 minutes.
Each zone sensor shows `ignored_readings` and `last_ignored`; the Now tab shows them too.

## Costs and flow temperature (0.5.0)

The **Costs & flow** tab and four sensors:

| Entity | Shows |
| --- | --- |
| `sensor.smart_heating_boiler_hours_today` | Boiler on-time today |
| `sensor.smart_heating_heating_cost_today` | Estimated gas cost today (hot-water base + burn rate × boiler hours, at the meter's unit rate and standing charge) |
| `sensor.smart_heating_heating_cost_next_7_days` | Forecast from boiler hours per degree-day and the weather forecast; `days` attribute per day |
| `sensor.smart_heating_suggested_flow_temperature` | Lowest flow that lets the primary room's radiators hold its goal (from learned cooling and warm-up) |

Each day's boiler hours and mean outdoor temperature are stored; the smart meter's "last full day" gas
is filed against its day when it arrives. Base and burn rate are learned after 4 days with differing
heating; the forecast after 4 full days. The meter and weather entities are set in Settings.

## Boost, frost, learning and notifications (0.6.0)

- **Frost protection is always on.** It is no longer a mode: in Auto, Manual and Off, any room
  below the frost temperature opens every radiator in the house and runs the boiler until that
  room is back above it. Opening them all gives the boiler one long burn rather than short runs
  for one room after another.
  A stored Frost mode becomes Off, which now does the same job.
- **Boost works in Manual mode.** Manual still leaves the radiators and boiler alone, except for
  boosts (and frost): a boosted room below its target (the boost's target, else the profile's
  target for that room, else the profile goal) opens its radiators and runs the boiler, with the
  usual minimum on/off times. When it reaches target or the boost ends, the radiators it opened
  close again, and the boiler goes off only if the boost turned it on.
- **Boost in Auto only shows while the boiler runs.** In Auto only the primary room starts the
  boiler, so a boost steers a run that is already going. The panel hides the button otherwise
  (the `boost` service still works for automations). A boost with no target on a room outside
  the profile now heats it to the profile goal rather than doing nothing.
- **Warm-up rate learns from each heating run.** It used to need the boiler on for a whole clock
  hour, which real 15–25 minute runs never managed. Now each run is used: the first 5 minutes are
  skipped while radiators heat up, then the room's temperature slope (with the cooling it would have
  done added back) gives °C/h per kW. Runs need 12 minutes of usable data and count by their length.
- **Cooling is learned in every room.** A room counts as cooling naturally once the boiler has been
  off 15 minutes, whether or not its valves are open, so the primary room (whose valves stay open
  between runs) learns too.
- **Notifications** (Settings → Notifications, both off by default): the boiler turning on or off
  (why, how long that run lasted, total today), and a daily summary at a chosen time (boiler time
  and runs today, estimated cost, tomorrow's forecast).
