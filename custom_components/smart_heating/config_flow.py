"""Config flow: pick the boiler switch, flow temperature source and phone."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BOILER,
    CONF_FLOW_SOURCE,
    CONF_NOTIFY,
    DEFAULT_BOILER,
    DEFAULT_FLOW_SOURCE,
    DEFAULT_NOTIFY,
    DOMAIN,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_BOILER, default=defaults.get(CONF_BOILER, DEFAULT_BOILER)):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
        vol.Required(CONF_FLOW_SOURCE, default=defaults.get(CONF_FLOW_SOURCE, DEFAULT_FLOW_SOURCE)):
            selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_number", "sensor"])),
        vol.Optional(CONF_NOTIFY, default=defaults.get(CONF_NOTIFY, DEFAULT_NOTIFY)):
            selector.TextSelector(),
    })


class SmartHeatingConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Smart Heating", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SmartHeatingOptionsFlow()


class SmartHeatingOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
