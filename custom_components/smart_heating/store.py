"""Turn the data model into plain dicts for HA's Store helper, and back.

Kept free of Home Assistant imports so it is tested with the controller.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from .model import Config, Mode, PriorityEntry, Profile, Radiator, Settings, SpareHeat, Zone


def config_to_dict(config: Config) -> dict[str, Any]:
    return {
        "radiators": [asdict(r) for r in config.radiators.values()],
        "zones": [asdict(z) for z in config.zones.values()],
        "profiles": [
            {**asdict(p), "spare_heat": p.spare_heat.value} for p in config.profiles.values()
        ],
        "settings": asdict(config.settings),
    }


def _pick(cls, data: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys the dataclass knows, so older/newer stores still load."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def config_from_dict(data: dict[str, Any]) -> Config:
    radiators = {r["name"]: Radiator(**_pick(Radiator, r)) for r in data.get("radiators", [])}
    zones = {z["name"]: Zone(**_pick(Zone, z)) for z in data.get("zones", [])}
    profiles = {}
    for p in data.get("profiles", []):
        raw = _pick(Profile, p)
        raw["priority"] = [PriorityEntry(**_pick(PriorityEntry, e)) for e in raw.get("priority", [])]
        raw["spare_heat"] = SpareHeat(raw.get("spare_heat", SpareHeat.BUFFER_ZONES.value))
        profiles[raw["name"]] = Profile(**raw)
    settings = Settings(**_pick(Settings, data.get("settings", {})))
    validate(Config(radiators, zones, profiles, settings))
    return Config(radiators, zones, profiles, settings)


def validate(config: Config) -> None:
    """Raise ValueError if anything points at something that does not exist."""
    for z in config.zones.values():
        for r in z.radiators:
            if r not in config.radiators:
                raise ValueError(f"Zone {z.name} lists unknown radiator {r}")
    for p in config.profiles.values():
        if p.primary_zone not in config.zones:
            raise ValueError(f"Profile {p.name} has unknown primary zone {p.primary_zone}")
        for e in p.priority:
            if e.zone not in config.zones:
                raise ValueError(f"Profile {p.name} lists unknown zone {e.zone}")
    if not config.profiles:
        raise ValueError("At least one profile is needed")


def state_to_dict(mode: Mode, profile: str, dry_run: bool) -> dict[str, Any]:
    return {"mode": mode.value, "profile": profile, "dry_run": dry_run}
