"""Light Layers: a real light shown through layers of light entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import async_handle_source_entity_changes
from homeassistant.helpers.storage import Store

from .const import CONF_LIGHT, DOMAIN

PLATFORMS = [Platform.LIGHT]


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
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
