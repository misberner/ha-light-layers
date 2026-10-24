"""Set up the layers of a real light."""

from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import CONF_LAYERS, CONF_LIGHT, CONF_PRIORITY, CONF_WALL_SWITCH, DOMAIN

PRIORITY = selector.NumberSelector(
    selector.NumberSelectorConfig(min=1, max=1000, step=1, mode=selector.NumberSelectorMode.BOX)
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
                    options={
                        CONF_WALL_SWITCH: user_input.get(CONF_WALL_SWITCH, False),
                        CONF_LAYERS: [],
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_LIGHT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light")
                ),
                vol.Optional(CONF_WALL_SWITCH, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LightLayersOptionsFlow()


class LightLayersOptionsFlow(OptionsFlow):
    """Add and remove layers; the wall switch setting."""

    @property
    def _layers(self) -> list[dict[str, Any]]:
        return list(self.config_entry.options.get(CONF_LAYERS, []))

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["add_layer", "settings"]
        if self._layers:
            menu.insert(1, "remove_layer")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_add_layer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        layers = self._layers
        errors = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not slugify(name) or slugify(name) in [slugify(la["name"]) for la in layers]:
                errors[CONF_NAME] = "duplicate"
            else:
                layer = {
                    "id": uuid.uuid4().hex[:12],
                    "name": name,
                    CONF_PRIORITY: int(user_input[CONF_PRIORITY]),
                }
                return self._save(**{CONF_LAYERS: [*layers, layer]})
        highest = max([la[CONF_PRIORITY] for la in layers], default=0)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_PRIORITY, default=highest + 10): PRIORITY,
            }
        )
        return self.async_show_form(step_id="add_layer", data_schema=schema, errors=errors)

    async def async_step_remove_layer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        layers = self._layers
        if user_input is not None:
            gone = set(user_input[CONF_LAYERS])
            return self._save(**{CONF_LAYERS: [la for la in layers if la["id"] not in gone]})
        options = [
            selector.SelectOptionDict(value=la["id"], label=f"{la['name']} ({la[CONF_PRIORITY]})")
            for la in layers
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_LAYERS, default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, multiple=True)
                )
            }
        )
        return self.async_show_form(step_id="remove_layer", data_schema=schema)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(**user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {vol.Required(CONF_WALL_SWITCH, default=options.get(CONF_WALL_SWITCH, False)): bool}
        )
        return self.async_show_form(step_id="settings", data_schema=schema)
