"""The layers of a real light: one light entity per layer, the real light shows the top."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_HS_COLOR,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import color as color_util
from homeassistant.util import slugify

from . import light_store, logic
from .const import BASE_SUFFIX, CONF_LAYERS, CONF_LIGHT, CONF_PRIORITY, CONF_WALL_SWITCH

_LOGGER = logging.getLogger(__name__)

# attribute of the real light's state that holds the colour of each colour mode
MODE_ATTR = {
    ColorMode.COLOR_TEMP: ATTR_COLOR_TEMP_KELVIN,
    ColorMode.HS: ATTR_HS_COLOR,
    ColorMode.XY: ATTR_XY_COLOR,
    ColorMode.RGB: ATTR_RGB_COLOR,
    ColorMode.RGBW: ATTR_RGBW_COLOR,
    ColorMode.RGBWW: ATTR_RGBWW_COLOR,
}
ATTR_MODE = {v: k for k, v in MODE_ATTR.items()}
UNSET = (None, STATE_UNAVAILABLE, STATE_UNKNOWN)
OWN_CONTEXTS = 20
SAVE_DELAY = 1.0
# how far the real light may be from what it should show and still show it
BRIGHTNESS_STEPS = 3
MIREDS = 5
HUE_DEGREES = 5.0
SATURATION = 5.0


def _seconds() -> float:
    return time.time()


def _minutes() -> float:
    return _seconds() / 60.0


def attrs_of(state: State) -> dict[str, Any]:
    """The attributes a real light shows: brightness, the colour of its mode, effect."""
    attrs = {}
    if (brightness := state.attributes.get(ATTR_BRIGHTNESS)) is not None:
        attrs[ATTR_BRIGHTNESS] = brightness
    key = MODE_ATTR.get(state.attributes.get(ATTR_COLOR_MODE))
    if key and state.attributes.get(key) is not None:
        value = state.attributes[key]
        attrs[key] = list(value) if isinstance(value, tuple) else value
    if state.attributes.get(ATTR_EFFECT) not in logic.NO_EFFECT:
        attrs[ATTR_EFFECT] = state.attributes[ATTR_EFFECT]
    return attrs


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    controller = Controller(hass, entry)
    await controller.async_start()
    entry.async_on_unload(controller.async_stop)
    real = entry.data[CONF_LIGHT]
    entities = [
        LayerLight(
            controller, logic.BASE, f"{entry.title} Base", entry.entry_id, f"{real}{BASE_SUFFIX}"
        )  # fmt: skip
    ]
    for layer in entry.options.get(CONF_LAYERS, []):
        entities.append(
            LayerLight(
                controller,
                layer["id"],
                f"{entry.title} {layer['name']}",
                f"{entry.entry_id}_{layer['id']}",
                f"{real}_{slugify(layer['name'])}",
            )
        )
    # on the real light's device, which is where people look for them
    device = async_entity_id_to_device(hass, real)
    for entity in entities:
        entity.device_entry = device
    # layers removed in the options are gone for good
    registry = er.async_get(hass)
    wanted = {e.unique_id for e in entities}
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg.unique_id not in wanted:
            registry.async_remove(reg.entity_id)
    async_add_entities(entities)


class Controller:
    """The layers of one real light: their state, and keeping the real light on the top one."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.data = {**entry.data, **entry.options}
        self.real = entry.data[CONF_LIGHT]
        self.state = logic.new_state()
        self.entities: dict[str, LayerLight] = {}
        self._own: list[str] = []  # contexts of our calls to the real light
        self._settled_at = 0.0  # reports until then are still our command arriving
        self._shown: State | None = None  # the real light's last report of our command
        self._adopt = False  # nothing restored: take the real light's state when it shows up
        self._cfg = logic.config()
        self._store = light_store(hass, entry.entry_id)
        self._unsub = None

    @property
    def real_state(self) -> State | None:
        return self.hass.states.get(self.real)

    @property
    def available(self) -> bool:
        st = self.real_state
        return st is not None and st.state != STATE_UNAVAILABLE

    def priorities(self) -> dict[str, int]:
        layers = self.data.get(CONF_LAYERS, [])
        return {layer["id"]: layer[CONF_PRIORITY] for layer in layers}

    def name_of(self, key: str) -> str:
        if key == logic.BASE:
            return "base"
        for layer in self.data.get(CONF_LAYERS, []):
            if layer["id"] == key:
                return layer["name"]
        return key

    # --- lifecycle ---------------------------------------------------------------

    async def async_start(self) -> None:
        stored = await self._store.async_load()
        st = self.real_state
        if stored and logic.BASE in (stored.get("layers") or {}):
            self.state = {"layers": stored["layers"], "applied": None}
            logic.configure(self.state, self.priorities())
            # no command at start-up when the real light already shows the target
            want = logic.target(self.state)
            if st is not None and st.state not in UNSET and _matches(st, want):
                self.state["applied"] = want
        elif st is not None and st.state not in UNSET:
            self._adopt_real(st)
        else:
            # the real light is not loaded yet (or unavailable): an empty base would
            # switch it off when it comes, so take whatever it shows then
            self._adopt = True
        logic.configure(self.state, self.priorities())
        self._unsub = async_track_state_change_event(self.hass, [self.real], self._real_changed)
        if self.available:
            await self.async_apply()

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await self._store.async_save(self._data())

    def _data(self) -> dict[str, Any]:
        return {"layers": self.state["layers"]}

    # --- commands ----------------------------------------------------------------

    async def async_command(
        self, key: str, action: str, kwargs: dict[str, Any], context: Context | None
    ) -> None:
        logic.command(self.state, key, action, kwargs, _minutes())
        await self.async_apply(kwargs.get(ATTR_TRANSITION), context)

    async def async_apply(
        self, transition: float | None = None, parent: Context | None = None
    ) -> None:
        """Bring the real light to the top layer, save, write the layers' states."""
        before = self.state["applied"]
        call = logic.step(self.state) if self.available else None
        if call is not None:
            service, data = call
            if service == "turn_on" and ATTR_EFFECT not in data and self._effect_on(before):
                if (stop := self._stop_effect()) is not None:
                    data[ATTR_EFFECT] = stop
            if transition is not None and self._features() & LightEntityFeature.TRANSITION:
                data[ATTR_TRANSITION] = transition
            if "brightness_pct" in data:
                data[ATTR_BRIGHTNESS] = round(data.pop("brightness_pct") * 255 / 100)
            context = Context(parent_id=parent.id if parent else None)
            self._own = (self._own + [context.id])[-OWN_CONTEXTS:]
            self._settled_at = (
                _seconds() + self._cfg["own_change_seconds"] + (data.get(ATTR_TRANSITION) or 0)
            )
            await self.hass.services.async_call(
                "light", service, {ATTR_ENTITY_ID: self.real, **data}, context=context
            )
        self._store.async_delay_save(self._data, SAVE_DELAY)
        self._write()

    @callback
    def _write(self) -> None:
        for entity in self.entities.values():
            if entity.hass is not None:
                entity.async_write_ha_state()

    # --- the real light ----------------------------------------------------------

    @callback
    def _real_changed(self, event: Event[EventStateChangedData]) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        if new is None or new.state in UNSET:
            self._write()  # unavailable
            return
        if self._adopt:
            self._adopt_real(new)
            self._write()
            return
        if old is None or old.state in UNSET:
            if self.data.get(CONF_WALL_SWITCH) and new.state == STATE_ON:
                # power back through the wall switch: someone switched it on there
                self._by_hand(new)
                return
            # back: show what it should, whatever it came back with
            self.state["applied"] = None
            self.hass.async_create_task(self.async_apply())
            return
        ours = (
            new.context.id in self._own
            # still settling after our command (e.g. Zigbee reports without our context)
            or _seconds() < self._settled_at
        )
        if ours or _same(new, self._shown) or _matches(new, self.state["applied"]):
            # our command, as the light renders it (a clipped colour, a rounded
            # brightness), or a report that changes nothing that is shown
            if ours or self._shown is None:
                self._shown = new
            base = self.state["layers"][logic.BASE]
            if logic.top(self.state) == logic.BASE and base["on"] and new.state == STATE_ON:
                logic.fill(self.state, attrs_of(new))
            return
        self._by_hand(new)

    @callback
    def _by_hand(self, new: State) -> None:
        self._shown = new
        # not through a layer: meant for the real light itself (its switch, the
        # vendor app, an automation for a button at it), so that is the base now
        _LOGGER.debug("%s changed by hand: the base, layers above off", self.real)
        logic.by_hand(self.state, new.state == STATE_ON, attrs_of(new))
        self.state["applied"] = logic.target(self.state)
        self._store.async_delay_save(self._data, SAVE_DELAY)
        self._write()

    def _features(self) -> LightEntityFeature:
        st = self.real_state
        return LightEntityFeature(st.attributes.get(ATTR_SUPPORTED_FEATURES, 0) if st else 0)

    def _effect_on(self, before: dict | None) -> bool:
        """Whether an effect was sent last or is running on the real light."""
        if before and before["attrs"].get(ATTR_EFFECT) not in logic.NO_EFFECT:
            return True
        st = self.real_state
        return st is not None and st.attributes.get(ATTR_EFFECT) not in logic.NO_EFFECT

    def _stop_effect(self) -> str | None:
        """The real light's own name for "no effect", if it has one."""
        st = self.real_state
        effects = (st.attributes.get(ATTR_EFFECT_LIST) if st else None) or []
        if not self._features() & LightEntityFeature.EFFECT:
            return None
        for name in logic.NO_EFFECT:
            if name in effects:
                return name
        return None

    def _adopt_real(self, st: State) -> None:
        self._adopt = False
        self.state = logic.new_state(st.state == STATE_ON, attrs_of(st))
        logic.configure(self.state, self.priorities())
        self.state["applied"] = logic.target(self.state)


class LayerLight(LightEntity):
    """One layer of the real light: the base (the normal state) or one above it."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(
        self, controller: Controller, key: str, name: str, unique_id: str, entity_id: str
    ) -> None:
        self._controller = controller
        self._key = key
        self._attr_name = name
        self._attr_unique_id = unique_id
        # named after the real light, not prefixed with area and device
        self.entity_id = entity_id
        # hidden: area, device and label targets ("all lights in the garden on") and
        # counts of lights reach the real light only, never a layer (which would stay
        # on); automations reach a layer by its entity id
        self._attr_entity_registry_visible_default = False
        controller.entities[key] = self

    @property
    def _layer(self) -> dict[str, Any]:
        return self._controller.state["layers"][self._key]

    @property
    def _attrs(self) -> dict[str, Any]:
        return self._layer["attrs"]

    @property
    def _real_state(self) -> State | None:
        return self._controller.real_state

    async def async_will_remove_from_hass(self) -> None:
        self._controller.entities.pop(self._key, None)

    # --- capabilities, from the real light ----------------------------------------

    @property
    def available(self) -> bool:
        return self._controller.available

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        st = self._real_state
        modes = st.attributes.get(ATTR_SUPPORTED_COLOR_MODES) if st else None
        return {ColorMode(m) for m in modes} if modes else {ColorMode.ONOFF}

    @property
    def supported_features(self) -> LightEntityFeature:
        return self._controller._features()

    @property
    def min_color_temp_kelvin(self) -> int:
        st = self._real_state
        return (st.attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN) if st else None) or 2000

    @property
    def max_color_temp_kelvin(self) -> int:
        st = self._real_state
        return (st.attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN) if st else None) or 6500

    @property
    def effect_list(self) -> list[str] | None:
        st = self._real_state
        return st.attributes.get(ATTR_EFFECT_LIST) if st else None

    # --- state, from the layer ----------------------------------------------------

    @property
    def is_on(self) -> bool:
        return self._layer["on"]

    @property
    def brightness(self) -> int | None:
        if ATTR_BRIGHTNESS in self._attrs:
            return self._attrs[ATTR_BRIGHTNESS]
        if (pct := self._attrs.get("brightness_pct")) is not None:
            return round(pct * 255 / 100)
        return None

    @property
    def color_mode(self) -> ColorMode:
        modes = self.supported_color_modes
        for key, mode in ATTR_MODE.items():
            if key in self._attrs and mode in modes:
                return mode
        if len(modes) == 1:
            return next(iter(modes))
        st = self._real_state
        real_mode = st.attributes.get(ATTR_COLOR_MODE) if st else None
        if real_mode in modes:
            return ColorMode(real_mode)
        for mode in (ColorMode.COLOR_TEMP, ColorMode.XY, ColorMode.HS, ColorMode.RGB):
            if mode in modes:
                return mode
        return next(iter(modes))

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._attrs.get(ATTR_COLOR_TEMP_KELVIN)

    @property
    def hs_color(self):
        return _tuple(self._attrs.get(ATTR_HS_COLOR))

    @property
    def xy_color(self):
        return _tuple(self._attrs.get(ATTR_XY_COLOR))

    @property
    def rgb_color(self):
        return _tuple(self._attrs.get(ATTR_RGB_COLOR))

    @property
    def rgbw_color(self):
        return _tuple(self._attrs.get(ATTR_RGBW_COLOR))

    @property
    def rgbww_color(self):
        return _tuple(self._attrs.get(ATTR_RGBWW_COLOR))

    @property
    def effect(self) -> str | None:
        return self._attrs.get(ATTR_EFFECT)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        shown = logic.top(self._controller.state)
        if self._key == logic.BASE:
            return {"real_light": self._controller.real, "showing": self._controller.name_of(shown)}
        return {"priority": self._layer["priority"], "showing": shown == self._key}

    # --- commands ----------------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._controller.async_command(self._key, "turn_on", kwargs, self._context)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._controller.async_command(self._key, "turn_off", kwargs, self._context)


def _matches(st: State, want: dict | None) -> bool:
    """Whether the real light shows *want*: on/off, brightness, colour and effect."""
    if want is None or (st.state == STATE_ON) != want["on"]:
        return False
    if not want["on"]:
        return True
    attrs = want["attrs"]
    target = attrs.get(ATTR_BRIGHTNESS)
    if target is None and (pct := attrs.get("brightness_pct")) is not None:
        target = round(pct * 255 / 100)
    have = st.attributes.get(ATTR_BRIGHTNESS)
    if target is not None and have is not None and abs(target - have) > BRIGHTNESS_STEPS:
        return False
    effect = st.attributes.get(ATTR_EFFECT)
    if _effect(attrs.get(ATTR_EFFECT)) != _effect(effect):
        return False
    if _effect(effect) is not None:
        return True  # an effect runs: its colours are its own
    if (kelvin := attrs.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
        return _kelvin_close(st, kelvin)
    if (hs := _hs_of(attrs, st)) is not None:
        if st.attributes.get(ATTR_COLOR_MODE) == ColorMode.COLOR_TEMP:
            return False
        return _hs_close(hs, st.attributes.get(ATTR_HS_COLOR))
    return True


def _same(new: State, old: State | None) -> bool:
    """Whether two reports of the real light show the same thing."""
    if old is None or new.state != old.state:
        return False
    if new.state != STATE_ON:
        return True
    a, b = new.attributes, old.attributes
    if a.get(ATTR_BRIGHTNESS) is not None and b.get(ATTR_BRIGHTNESS) is not None:
        if abs(a[ATTR_BRIGHTNESS] - b[ATTR_BRIGHTNESS]) > BRIGHTNESS_STEPS:
            return False
    if _effect(a.get(ATTR_EFFECT)) != _effect(b.get(ATTR_EFFECT)):
        return False
    if _effect(a.get(ATTR_EFFECT)) is not None:
        return True
    if a.get(ATTR_COLOR_MODE) != b.get(ATTR_COLOR_MODE):
        return False
    if a.get(ATTR_COLOR_MODE) == ColorMode.COLOR_TEMP:
        kelvin = b.get(ATTR_COLOR_TEMP_KELVIN)
        return kelvin is None or _kelvin_close(new, kelvin)
    if b.get(ATTR_HS_COLOR) is not None:
        return _hs_close(b[ATTR_HS_COLOR], a.get(ATTR_HS_COLOR))
    return True


def _effect(value):
    return None if value in logic.NO_EFFECT else value


def _kelvin_close(st: State, kelvin: float) -> bool:
    have = st.attributes.get(ATTR_COLOR_TEMP_KELVIN)
    if have is None:
        return False
    # the light clamps to its range
    low = st.attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN) or kelvin
    high = st.attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN) or kelvin
    kelvin = min(max(kelvin, low), high)
    return abs(1e6 / kelvin - 1e6 / have) <= MIREDS


def _hs_of(attrs: dict, st: State):
    """The colour in *attrs* as hue/saturation, or None for no colour."""
    if (hs := attrs.get(ATTR_HS_COLOR)) is not None:
        return tuple(hs)
    if (xy := attrs.get(ATTR_XY_COLOR)) is not None:
        return color_util.color_xy_to_hs(*xy)
    if (rgb := attrs.get(ATTR_RGB_COLOR)) is not None:
        return color_util.color_RGB_to_hs(*rgb)
    if (rgbw := attrs.get(ATTR_RGBW_COLOR)) is not None:
        return color_util.color_RGB_to_hs(*color_util.color_rgbw_to_rgb(*rgbw))
    if (rgbww := attrs.get(ATTR_RGBWW_COLOR)) is not None:
        low = st.attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN) or 2000
        high = st.attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN) or 6500
        return color_util.color_RGB_to_hs(*color_util.color_rgbww_to_rgb(*rgbww, low, high))
    return None


def _hs_close(want, have) -> bool:
    if have is None:
        return False
    if max(want[1], have[1]) < SATURATION:
        return True  # (nearly) white: the hue says nothing
    hue = abs(want[0] - have[0]) % 360
    return min(hue, 360 - hue) <= HUE_DEGREES and abs(want[1] - have[1]) <= SATURATION


def _tuple(value):
    return tuple(value) if value is not None else None
