"""The layered light: stands in for a real light, its state is the base."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import voluptuous as vol
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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import color as color_util

from . import logic
from .const import (
    ATTR_KEY,
    ATTR_OFF,
    ATTR_PRIORITY,
    CONF_LIGHT,
    CONF_MINUTES,
    CONF_WALL_SWITCH,
    DEFAULT_KEY,
    DEFAULT_MINUTES,
    SERVICE_CANCEL,
    SERVICE_OVERRIDE,
)

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
# how far the real light may be from what it should show and still show it
BRIGHTNESS_STEPS = 3
MIREDS = 5
HUE_DEGREES = 5.0
SATURATION = 5.0

OVERRIDE_SCHEMA = {
    vol.Optional(ATTR_KEY, default=DEFAULT_KEY): cv.string,
    vol.Optional(CONF_MINUTES): vol.All(vol.Coerce(float), vol.Range(min=0.05)),
    vol.Optional(ATTR_PRIORITY, default=0): vol.Coerce(int),
    vol.Optional(ATTR_OFF, default=False): cv.boolean,
    vol.Exclusive(ATTR_BRIGHTNESS, "brightness"): vol.All(vol.Coerce(int), vol.Range(0, 255)),
    vol.Exclusive("brightness_pct", "brightness"): vol.All(vol.Coerce(float), vol.Range(0, 100)),
    vol.Exclusive(ATTR_COLOR_TEMP_KELVIN, "colour"): vol.Coerce(int),
    vol.Exclusive(ATTR_HS_COLOR, "colour"): vol.All(vol.ExactSequence((vol.Coerce(float),) * 2)),
    vol.Exclusive(ATTR_XY_COLOR, "colour"): vol.All(vol.ExactSequence((vol.Coerce(float),) * 2)),
    vol.Exclusive(ATTR_RGB_COLOR, "colour"): vol.All(vol.ExactSequence((cv.byte,) * 3)),
    vol.Optional(ATTR_EFFECT): cv.string,
}
CANCEL_SCHEMA = {vol.Optional(ATTR_KEY): cv.string}


def _seconds() -> float:
    return time.time()


def _minutes() -> float:
    return _seconds() / 60.0


def attrs_of(state: State) -> dict[str, Any]:
    """The base attributes a real light shows: brightness, the colour of its mode, effect."""
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


class LayerData(ExtraStoredData):
    def __init__(self, layer: dict) -> None:
        self.layer = layer

    def as_dict(self) -> dict[str, Any]:
        return {"layer": self.layer}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entity = LayeredLight(entry)
    # on the real light's device: area and device targets reach the stand-in, and
    # skip the real light once it is hidden
    entity.device_entry = async_entity_id_to_device(hass, entry.data[CONF_LIGHT])
    async_add_entities([entity])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_OVERRIDE, OVERRIDE_SCHEMA, "async_override")
    platform.async_register_entity_service(SERVICE_CANCEL, CANCEL_SCHEMA, "async_cancel")


class LayeredLight(LightEntity, RestoreEntity):
    """Commands set the base; the real light shows the top override or the base."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._real = entry.data[CONF_LIGHT]
        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id
        self._layer = logic.new_layer()
        self._own: list[str] = []  # contexts of our calls to the real light
        self._settled_at = 0.0  # reports until then are still our command arriving
        self._shown: State | None = None  # the real light's last report of our command
        self._unsub_end = None
        self._cfg = logic.config()
        self._adopt = False  # nothing restored: take the real light's state when it shows up

    # --- capabilities and state, from the real light and the base ----------------

    @property
    def _real_state(self) -> State | None:
        return self.hass.states.get(self._real)

    @property
    def available(self) -> bool:
        st = self._real_state
        return st is not None and st.state != STATE_UNAVAILABLE

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        st = self._real_state
        modes = st.attributes.get(ATTR_SUPPORTED_COLOR_MODES) if st else None
        return {ColorMode(m) for m in modes} if modes else {ColorMode.ONOFF}

    @property
    def supported_features(self) -> LightEntityFeature:
        st = self._real_state
        return LightEntityFeature(st.attributes.get(ATTR_SUPPORTED_FEATURES, 0) if st else 0)

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

    @property
    def is_on(self) -> bool:
        return self._layer["base"]["on"]

    @property
    def _base(self) -> dict[str, Any]:
        return self._layer["base"]["attrs"]

    @property
    def brightness(self) -> int | None:
        if ATTR_BRIGHTNESS in self._base:
            return self._base[ATTR_BRIGHTNESS]
        if (pct := self._base.get("brightness_pct")) is not None:
            return round(pct * 255 / 100)
        return None

    @property
    def color_mode(self) -> ColorMode:
        modes = self.supported_color_modes
        for key, mode in ATTR_MODE.items():
            if key in self._base and mode in modes:
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
        return self._base.get(ATTR_COLOR_TEMP_KELVIN)

    @property
    def hs_color(self):
        return _tuple(self._base.get(ATTR_HS_COLOR))

    @property
    def xy_color(self):
        return _tuple(self._base.get(ATTR_XY_COLOR))

    @property
    def rgb_color(self):
        return _tuple(self._base.get(ATTR_RGB_COLOR))

    @property
    def rgbw_color(self):
        return _tuple(self._base.get(ATTR_RGBW_COLOR))

    @property
    def rgbww_color(self):
        return _tuple(self._base.get(ATTR_RGBWW_COLOR))

    @property
    def effect(self) -> str | None:
        return self._base.get(ATTR_EFFECT)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        top = logic.active(self._layer, _minutes())
        overrides = {
            key: {
                "until": _iso(o["until"]),
                "priority": o["priority"],
                **({"off": True} if o["attrs"].get("on") is False else o["attrs"]),
            }
            for key, o in sorted(self._layer["overrides"].items())
        }
        return {
            "real_light": self._real,
            "showing": f"override {top[0]}" if top else "base",
            "overrides": overrides,
        }

    @property
    def extra_restore_state_data(self) -> LayerData:
        return LayerData(self._layer)

    # --- lifecycle ---------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._cfg = logic.config()
        restored = await self.async_get_last_extra_data()
        layer = restored.as_dict().get("layer") if restored else None
        if layer and "base" in layer:
            self._layer = {
                "base": layer["base"],
                "overrides": layer.get("overrides") or {},
                "applied": None,
            }
            # no command at start-up when the real light already shows the target
            st = self._real_state
            want = logic.target(self._layer, _minutes())
            if st is not None and st.state not in UNSET and _matches(st, want):
                self._layer["applied"] = want
        elif (st := self._real_state) is not None and st.state not in UNSET:
            self._adopt_real(st)
        else:
            # the real light is not loaded yet (or unavailable): an empty base would
            # switch it off when it comes, so take whatever it shows then
            self._adopt = True
        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._real], self._real_changed)
        )
        self.async_on_remove(
            async_track_entity_registry_updated_event(self.hass, self._real, self._real_renamed)
        )
        self.async_on_remove(self._cancel_end_timer)
        if self.available:
            await self._apply()
        else:
            self._schedule_end()

    # --- commands ----------------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        logic.command(self._layer, "turn_on", kwargs)
        await self._apply(kwargs.get(ATTR_TRANSITION))

    async def async_turn_off(self, **kwargs: Any) -> None:
        logic.command(self._layer, "turn_off")
        await self._apply(kwargs.get(ATTR_TRANSITION))

    async def async_override(self, **kwargs: Any) -> None:
        key = kwargs.pop(ATTR_KEY, DEFAULT_KEY)
        minutes = kwargs.pop(CONF_MINUTES, None)
        if minutes is None:
            minutes = self._entry.options.get(CONF_MINUTES, DEFAULT_MINUTES)
        priority = kwargs.pop(ATTR_PRIORITY, 0)
        if kwargs.pop(ATTR_OFF, False):
            attrs = {"on": False}
        else:
            attrs = {k: (list(v) if isinstance(v, tuple) else v) for k, v in kwargs.items()}
            if "brightness_pct" in attrs:
                attrs[ATTR_BRIGHTNESS] = round(attrs.pop("brightness_pct") * 255 / 100)
        st = self._real_state
        showing_base = logic.active(self._layer, _minutes()) is None
        if showing_base and self.is_on and st is not None and st.state == STATE_ON:
            logic.fill(self._layer, attrs_of(st))
        logic.override(self._layer, key, attrs, minutes, _minutes(), priority)
        await self._apply()

    async def async_cancel(self, **kwargs: Any) -> None:
        logic.cancel(self._layer, kwargs.get(ATTR_KEY))
        await self._apply()

    async def _apply(self, transition: float | None = None) -> None:
        """Bring the real light to the target, write our state, time the next end."""
        before = self._layer["applied"]
        call = logic.step(self._layer, _minutes()) if self.available else None
        if call is not None:
            service, data = call
            if service == "turn_on" and ATTR_EFFECT not in data and self._effect_on(before):
                if (stop := self._stop_effect()) is not None:
                    data[ATTR_EFFECT] = stop
            if transition is not None and self.supported_features & LightEntityFeature.TRANSITION:
                data[ATTR_TRANSITION] = transition
            if "brightness_pct" in data:
                data[ATTR_BRIGHTNESS] = round(data.pop("brightness_pct") * 255 / 100)
            context = Context(parent_id=self._context.id if self._context else None)
            self._own = (self._own + [context.id])[-OWN_CONTEXTS:]
            self._settled_at = (
                _seconds() + self._cfg["own_change_seconds"] + (data.get(ATTR_TRANSITION) or 0)
            )
            await self.hass.services.async_call(
                "light", service, {ATTR_ENTITY_ID: self._real, **data}, context=context
            )
        self._schedule_end()
        self.async_write_ha_state()

    # --- the real light and the clock --------------------------------------------

    @callback
    def _real_changed(self, event: Event[EventStateChangedData]) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        if new is None or new.state in UNSET:
            self.async_write_ha_state()  # unavailable
            return
        if self._adopt:
            self._adopt_real(new)
            self._schedule_end()
            self.async_write_ha_state()
            return
        if old is None or old.state in UNSET:
            if self._entry.options.get(CONF_WALL_SWITCH) and new.state == STATE_ON:
                # power back through the wall switch: someone switched it on there
                self._by_hand(new)
                return
            # back: show what it should, whatever it came back with
            self._layer["applied"] = None
            self.hass.async_create_task(self._apply())
            return
        ours = (
            new.context.id in self._own
            # still settling after our command (e.g. Zigbee reports without our context)
            or _seconds() < self._settled_at
        )
        if ours or _same(new, self._shown) or _matches(new, self._layer["applied"]):
            # our command, as the light renders it (a clipped colour, a rounded
            # brightness), or a report that changes nothing that is shown
            if ours or self._shown is None:
                self._shown = new
            showing_base = logic.active(self._layer, _minutes()) is None
            if showing_base and self.is_on and new.state == STATE_ON:
                logic.fill(self._layer, attrs_of(new))
            return
        self._by_hand(new)

    @callback
    def _by_hand(self, new: State) -> None:
        self._shown = new
        on = new.state == STATE_ON
        # not through the stand-in: meant for the real light itself (its switch, the
        # vendor app, an automation for a button at it), so that is the base now
        _LOGGER.debug("%s: %s changed by hand, ending overrides", self.entity_id, self._real)
        logic.by_hand(self._layer, on, attrs_of(new))
        self._layer["applied"] = logic.target(self._layer, _minutes())
        self._schedule_end()
        self.async_write_ha_state()

    def _effect_on(self, before: dict | None) -> bool:
        """Whether an effect was sent last or is running on the real light."""
        if before and before["attrs"].get(ATTR_EFFECT) not in logic.NO_EFFECT:
            return True
        st = self._real_state
        return st is not None and st.attributes.get(ATTR_EFFECT) not in logic.NO_EFFECT

    def _stop_effect(self) -> str | None:
        """The real light's own name for "no effect", if it has one."""
        effects = self.effect_list or []
        if not self.supported_features & LightEntityFeature.EFFECT:
            return None
        for name in logic.NO_EFFECT:
            if name in effects:
                return name
        return None

    @callback
    def _real_renamed(self, event) -> None:
        """The real light got a new entity id: follow it (the entry reloads)."""
        data = event.data
        if data["action"] != "update" or "old_entity_id" not in data:
            return
        new_id = data["entity_id"]
        _LOGGER.debug("%s: %s renamed to %s", self.entity_id, self._real, new_id)
        self.hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, CONF_LIGHT: new_id}, unique_id=new_id
        )

    def _adopt_real(self, st: State) -> None:
        self._adopt = False
        self._layer = logic.new_layer(st.state == STATE_ON, attrs_of(st))
        self._layer["applied"] = logic.target(self._layer, _minutes())

    def _schedule_end(self) -> None:
        self._cancel_end_timer()
        end = logic.next_end(self._layer)
        if end is None:
            return
        when = datetime.fromtimestamp(end * 60 + 0.5, tz=UTC)
        self._unsub_end = async_track_point_in_utc_time(self.hass, self._override_ended, when)

    @callback
    def _cancel_end_timer(self) -> None:
        if self._unsub_end is not None:
            self._unsub_end()
            self._unsub_end = None

    @callback
    def _override_ended(self, _now: datetime) -> None:
        self._unsub_end = None
        self.hass.async_create_task(self._apply())


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


def _iso(minutes: float) -> str:
    return datetime.fromtimestamp(minutes * 60, tz=UTC).isoformat()
