"""The base taking over the real light's entity id, and giving it back.

Taking over: the real light becomes <id>_raw and hidden; the base gets the old
id, so automations, scenes and dashboards keep working and now go through the
layers.  Labels, aliases and voice-assistant exposure move to the base (hidden
entities get no label targets, and the base is what people should talk to).
Giving back, when the light is removed from the integration, reverses it: the
real light gets its id back, visible, with the base's labels and exposure.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

RAW_SUFFIX = "_raw"


def _free(registry: er.EntityRegistry, hass: HomeAssistant, wanted: str) -> str:
    entity_id, n = wanted, 2
    while registry.async_get(entity_id) is not None or hass.states.get(entity_id) is not None:
        entity_id, n = f"{wanted}_{n}", n + 1
    return entity_id


@callback
def take_over(hass: HomeAssistant, light: str) -> str:
    """Rename and hide the real light; returns its new entity id."""
    registry = er.async_get(hass)
    raw = _free(registry, hass, f"{light}{RAW_SUFFIX}")
    registry.async_update_entity(
        light, new_entity_id=raw, hidden_by=er.RegistryEntryHider.INTEGRATION
    )
    return raw


def _exposure(entry: er.RegistryEntry) -> dict[str, bool]:
    return {
        assistant: options["should_expose"]
        for assistant, options in entry.options.items()
        if isinstance(options, dict) and "should_expose" in options
    }


@callback
def keep(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    """What the base carries that the real light gets back: labels, exposure."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    return {"labels": sorted(entry.labels), "exposure": _exposure(entry)}


@callback
def move_to_base(hass: HomeAssistant, raw: str, base: str) -> None:
    """Whatever the hidden real light carries belongs on the base."""
    registry = er.async_get(hass)
    src, dst = registry.async_get(raw), registry.async_get(base)
    if src is None or dst is None:
        return
    exposure = _exposure(src)
    if not (src.labels or src.aliases or any(exposure.values())):
        return
    aliases = list(dst.aliases) + [a for a in src.aliases if a not in dst.aliases]
    registry.async_update_entity(base, labels=set(dst.labels) | set(src.labels),
                                 aliases=aliases)  # fmt: skip
    registry.async_update_entity(raw, labels=set(), aliases=type(src.aliases)())
    for assistant, exposed in exposure.items():
        if exposed:
            registry.async_update_entity_options(base, assistant, {"should_expose": True})
        registry.async_update_entity_options(raw, assistant, {"should_expose": False})


@callback
def give_back(hass: HomeAssistant, raw: str, original: str, kept: dict[str, Any] | None) -> None:
    """The real light gets its entity id back, visible, with the base's labels and exposure.

    The base must be gone from the registry by now (its id is the one given back).
    """
    registry = er.async_get(hass)
    entry = registry.async_get(raw)
    if entry is None:
        return  # the real light is gone itself
    new_id = original if registry.async_get(original) is None else raw
    labels = set(entry.labels) | set((kept or {}).get("labels") or [])
    registry.async_update_entity(raw, new_entity_id=new_id, hidden_by=None, labels=labels)
    for assistant, exposed in ((kept or {}).get("exposure") or {}).items():
        registry.async_update_entity_options(new_id, assistant, {"should_expose": exposed})
