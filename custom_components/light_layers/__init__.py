"""Light Layers: real lights shown through layers of light entities.

One config entry; each layered light is a subentry of it ("Add light"), with
its layers configured there.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from . import takeover
from .const import CONF_ENTITY_ID, CONF_LIGHT, CONF_TAKE_OVER, DOMAIN, SUBENTRY_LIGHT

PLATFORMS = [Platform.LIGHT]
SAVE_DELAY = 1.0


def light_store(hass: HomeAssistant, subentry_id: str) -> Store:
    """The layers' state of one light."""
    return Store(hass, 1, f"{DOMAIN}.{subentry_id}")


def _known_store(hass: HomeAssistant) -> Store:
    """The lights set up so far: to give back what a removed one took over."""
    return Store(hass, 1, f"{DOMAIN}.lights")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = _known_store(hass)
    known: dict[str, dict[str, Any]] = await store.async_load() or {}
    lights = {
        sid: sub for sid, sub in entry.subentries.items() if sub.subentry_type == SUBENTRY_LIGHT
    }
    # lights removed since: give the real light back.  Home Assistant reloads the entry
    # before it clears the removed light's entities, so clear them here first.
    registry = er.async_get(hass)
    for sid in [s for s in known if s not in lights]:
        info = known.pop(sid)
        kept = info.get("kept")
        if info.get(CONF_TAKE_OVER):
            kept = takeover.keep(hass, info[CONF_ENTITY_ID]) or kept
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg.config_subentry_id == sid:
                registry.async_remove(reg.entity_id)
        if info.get(CONF_TAKE_OVER):
            takeover.give_back(hass, info[CONF_LIGHT], info[CONF_ENTITY_ID], kept)
        await light_store(hass, sid).async_remove()
    for sid, sub in lights.items():
        info = known.setdefault(sid, {})
        info.update({k: sub.data.get(k) for k in (CONF_LIGHT, CONF_TAKE_OVER, CONF_ENTITY_ID)})
        if sub.data.get(CONF_TAKE_OVER) and (kept := takeover.keep(hass, sub.data[CONF_ENTITY_ID])):
            info["kept"] = kept
    await store.async_save(known)

    @callback
    def registry_updated(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        if event.data["action"] != "update":
            return
        entity_id = event.data["entity_id"]
        old = event.data.get("old_entity_id")
        for sid, sub in lights.items():
            if old is not None and old == sub.data.get(CONF_LIGHT):
                # the real light got a new entity id: follow it (the entry reloads)
                hass.config_entries.async_update_subentry(
                    entry, sub, data={**sub.data, CONF_LIGHT: entity_id}
                )
            elif sub.data.get(CONF_TAKE_OVER) and entity_id == sub.data.get(CONF_ENTITY_ID):
                # labels or exposure of the base changed: what the real light gets back
                if (kept := takeover.keep(hass, entity_id)) is not None:
                    known[sid]["kept"] = kept
                    store.async_delay_save(lambda: known, SAVE_DELAY)

    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The whole integration is removed: every real light gets its id back."""
    registry = er.async_get(hass)
    for sid, sub in entry.subentries.items():
        await light_store(hass, sid).async_remove()
        if not sub.data.get(CONF_TAKE_OVER):
            continue
        kept = takeover.keep(hass, sub.data[CONF_ENTITY_ID])
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg.config_subentry_id == sid:
                registry.async_remove(reg.entity_id)
        takeover.give_back(hass, sub.data[CONF_LIGHT], sub.data[CONF_ENTITY_ID], kept)
    await _known_store(hass).async_remove()
