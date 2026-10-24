"""Light Layers: a real light shown through layers of light entities."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_FLOOR_ID,
    ATTR_LABEL_ID,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import async_handle_source_entity_changes
from homeassistant.helpers.storage import Store
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.helpers.typing import ConfigType

from .const import CONF_LIGHT, DOMAIN

PLATFORMS = [Platform.LIGHT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
# light.turn_on, turn_off and toggle, with a real light standing for its base
SERVICES = ("turn_on", "turn_off", "toggle")
TARGET_KEYS = (ATTR_ENTITY_ID, ATTR_DEVICE_ID, ATTR_AREA_ID, ATTR_FLOOR_ID, ATTR_LABEL_ID)


def light_store(hass: HomeAssistant, entry_id: str) -> Store:
    """The layers' state of one light."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def forward(call: ServiceCall) -> None:
        await _forward(hass, call)

    # the light service's own fields, checked by it
    schema = cv.make_entity_service_schema({}, extra=vol.ALLOW_EXTRA)
    for name in SERVICES:
        hass.services.async_register(DOMAIN, name, forward, schema=schema)
    return True


def base_layers(hass: HomeAssistant) -> dict[str, str]:
    """The base layer of each real light with layers: {real light: base}."""
    registry = er.async_get(hass)
    bases = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue  # its layers are unavailable: the real light itself then
        if base := registry.async_get_entity_id("light", DOMAIN, entry.entry_id):
            bases[entry.data[CONF_LIGHT]] = base
    return bases


async def _forward(hass: HomeAssistant, call: ServiceCall) -> None:
    """The light service on the lights the target names, a real light with layers
    replaced by its base: area, device and label targets name the real lights,
    since the layers are hidden."""
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))
    named = selected.referenced | selected.indirectly_referenced
    bases = base_layers(hass)
    lights = sorted({bases.get(e, e) for e in named if e.startswith("light.")})
    if not lights:
        return
    data = {k: v for k, v in call.data.items() if k not in TARGET_KEYS}
    await hass.services.async_call(
        "light",
        call.service,
        {ATTR_ENTITY_ID: lights, **data},
        blocking=True,
        context=call.context,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    real = entry.data[CONF_LIGHT]

    def follow(entity_id: str) -> None:
        # the real light got a new entity id (the entry reloads)
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_LIGHT: entity_id}, unique_id=entity_id
        )

    # renames, and the real light moving to another device (the layers sit on its device)
    entry.async_on_unload(
        async_handle_source_entity_changes(
            hass,
            helper_config_entry_id=entry.entry_id,
            set_source_entity_id_or_uuid=follow,
            source_device_id=async_entity_id_to_device_id(hass, real),
            source_entity_id_or_uuid=real,
        )
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await light_store(hass, entry.entry_id).async_remove()
