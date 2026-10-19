"""Set up a layered light in front of a real one."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import CONF_LIGHT, CONF_MINUTES, DEFAULT_MINUTES, DOMAIN

MINUTES = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0.5, max=240, step=0.5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX
    )
)


class LightLayersConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            entry = er.async_get(self.hass).async_get(user_input[CONF_LIGHT])
            if entry is not None and entry.platform == DOMAIN:
                errors[CONF_LIGHT] = "layered"
            else:
                await self.async_set_unique_id(user_input[CONF_LIGHT])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_LIGHT: user_input[CONF_LIGHT]},
                    options={CONF_MINUTES: user_input.get(CONF_MINUTES, DEFAULT_MINUTES)},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_LIGHT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light")
                ),
                vol.Optional(CONF_MINUTES, default=DEFAULT_MINUTES): MINUTES,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LightLayersOptionsFlow()


class LightLayersOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_MINUTES, DEFAULT_MINUTES)
        schema = vol.Schema({vol.Required(CONF_MINUTES, default=current): MINUTES})
        return self.async_show_form(step_id="init", data_schema=schema)
