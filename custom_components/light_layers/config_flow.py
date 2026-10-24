"""Set up Light Layers, and the layered lights in it (subentries)."""

from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.util import slugify

from . import takeover
from .const import (
    CONF_ENTITY_ID,
    CONF_LAYER,
    CONF_LAYERS,
    CONF_LIGHT,
    CONF_PRIORITY,
    CONF_TAKE_OVER,
    CONF_WALL_SWITCH,
    DOMAIN,
    FIRST_PRIORITY,
    SUBENTRY_LIGHT,
)

PRIORITY = selector.NumberSelector(
    selector.NumberSelectorConfig(min=1, max=1000, step=1, mode=selector.NumberSelectorMode.BOX)
)


class LightLayersConfigFlow(ConfigFlow, domain=DOMAIN):
    """The integration itself: empty, lights are added to it."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="Light Layers", data={})

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_LIGHT: LayeredLightFlow}


def _layer(name: str, priority: int) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex[:12], "name": name, CONF_PRIORITY: priority}


class LayeredLightFlow(ConfigSubentryFlow):
    """Add a layered light; reconfigure: add and remove its layers, settings."""

    # --- adding a light ------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors = {}
        if user_input is not None:
            light = user_input[CONF_LIGHT]
            reg = er.async_get(self.hass).async_get(light)
            take_over = user_input.get(CONF_TAKE_OVER, True)
            name = (user_input.get(CONF_NAME) or "").strip() or self._name_of(light)
            layer = (user_input.get(CONF_LAYER) or "").strip()
            if reg is not None and reg.platform == DOMAIN:
                errors[CONF_LIGHT] = "layered"
            elif self._configured(light):
                return self.async_abort(reason="already_configured")
            elif take_over and reg is None:
                errors[CONF_LIGHT] = "not_in_registry"  # no unique id: cannot be renamed
            else:
                data: dict[str, Any] = {CONF_LIGHT: light}
                if take_over:
                    # the base takes the real light's id; the real light becomes <id>_raw
                    raw = takeover.take_over(self.hass, light)
                    data = {CONF_LIGHT: raw, CONF_TAKE_OVER: True, CONF_ENTITY_ID: light}
                data[CONF_WALL_SWITCH] = user_input.get(CONF_WALL_SWITCH, False)
                data[CONF_LAYERS] = [_layer(layer, FIRST_PRIORITY)] if layer else []
                return self.async_create_entry(title=name, data=data, unique_id=data[CONF_LIGHT])
        schema = vol.Schema(
            {
                vol.Required(CONF_LIGHT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="light")
                ),
                vol.Optional(CONF_NAME): str,
                vol.Optional(CONF_TAKE_OVER, default=True): bool,
                vol.Optional(CONF_LAYER): str,
                vol.Optional(CONF_WALL_SWITCH, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    def _configured(self, light: str) -> bool:
        for sub in self._get_entry().subentries.values():
            if light in (sub.data.get(CONF_LIGHT), sub.data.get(CONF_ENTITY_ID)):
                return True
        return False

    def _name_of(self, light: str) -> str:
        reg = er.async_get(self.hass).async_get(light)
        if reg is not None and (reg.name or reg.original_name):
            return reg.name or reg.original_name
        state = self.hass.states.get(light)
        if state is not None and state.attributes.get("friendly_name"):
            return state.attributes["friendly_name"]
        return light.split(".", 1)[1].replace("_", " ").title()

    # --- reconfiguring a light -------------------------------------------------------

    @property
    def _data(self) -> dict[str, Any]:
        return dict(self._get_reconfigure_subentry().data)

    def _save(self, **changes: Any) -> SubentryFlowResult:
        return self.async_update_and_abort(
            self._get_entry(), self._get_reconfigure_subentry(), data={**self._data, **changes}
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        menu = ["add_layer", "settings"]
        if self._data.get(CONF_LAYERS):
            menu.insert(1, "remove_layer")
        return self.async_show_menu(step_id="reconfigure", menu_options=menu)

    async def async_step_add_layer(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        layers = list(self._data.get(CONF_LAYERS, []))
        errors = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not slugify(name) or slugify(name) in [slugify(la["name"]) for la in layers]:
                errors[CONF_NAME] = "duplicate"
            else:
                layer = _layer(name, int(user_input[CONF_PRIORITY]))
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
    ) -> SubentryFlowResult:
        layers = list(self._data.get(CONF_LAYERS, []))
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
    ) -> SubentryFlowResult:
        if user_input is not None:
            return self._save(**user_input)
        schema = vol.Schema(
            {vol.Required(CONF_WALL_SWITCH, default=self._data.get(CONF_WALL_SWITCH, False)): bool}
        )
        return self.async_show_form(step_id="settings", data_schema=schema)
