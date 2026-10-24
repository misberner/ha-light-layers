"""A real light shown through layers of light entities (no Home Assistant here).

Every layer is a light of its own.  The base layer is the light's normal
state: schedules, scenes, dashboards and voice use it like the real light,
and it says on or off.  The layers above it each have a priority; one that is
off is transparent, one that is on shows itself over everything below.  The
real light shows the highest layer that is on (the most recently switched on
among equals), with what it leaves open (a layer that only sets a brightness)
taken from the base, or the base itself.

Commands to a layer that is not shown are recorded, not shown: when the
layers above go off, the light shows the state it should be in now, not the
one it had before.

State: {"layers": {id: {"on", "attrs", "priority", "on_at"}}, "applied": the
target last sent to the real light}; the base is BASE, priority below all.
"""

BASE = "base"

DEFAULTS = {
    "own_change_seconds": 10.0,  # real-light reports this soon after a command are ours
}

# attributes a light remembers between on and off; any one replaces the
# others of its kind (setting a colour temperature drops an xy colour)
COLOUR = ("color_temp_kelvin", "hs_color", "xy_color", "rgb_color", "rgbw_color", "rgbww_color")
BRIGHTNESS = ("brightness", "brightness_pct")
KEPT = BRIGHTNESS + COLOUR + ("effect",)
# effect values that mean "no effect" (WLED's plain mode is "Solid")
NO_EFFECT = (None, "", "off", "Off", "none", "None", "Solid", "stop_effect", "finish_effect")


def config(overrides=None):
    cfg = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key not in DEFAULTS:
            raise KeyError(f"light_layers: unknown setting {key!r}")
        cfg[key] = value
    return cfg


def _layer(on=False, attrs=None, priority=0):
    return {"on": on, "attrs": dict(attrs or {}), "priority": priority, "on_at": 0.0}


def new_state(on=False, attrs=None):
    return {"layers": {BASE: _layer(on, attrs)}, "applied": None}


def configure(state, priorities):
    """Bring the layers in line with the configured ones ({id: priority}).

    New layers start off (transparent); removed ones go; the base stays.
    """
    layers = state["layers"]
    layers.setdefault(BASE, _layer())
    for key in [k for k in layers if k != BASE and k not in priorities]:
        del layers[key]
    for key, priority in priorities.items():
        layers.setdefault(key, _layer())["priority"] = priority


def _merge(attrs, update):
    out = dict(attrs)
    for group in (BRIGHTNESS, COLOUR):
        if any(k in update for k in group):
            for k in group:
                out.pop(k, None)
    out.update({k: v for k, v in update.items() if k in KEPT and v is not None})
    if out.get("effect") in NO_EFFECT:
        out.pop("effect", None)
    return out


def command(state, key, action, data=None, now=0.0):
    """turn_on / turn_off / toggle of one layer."""
    layer = state["layers"][key]
    if action == "toggle":
        action = "turn_off" if layer["on"] else "turn_on"
    if action == "turn_off":
        layer["on"] = False
        return
    if not layer["on"]:
        layer["on_at"] = now
    layer["on"] = True
    layer["attrs"] = _merge(layer["attrs"], data or {})


def fill(state, attrs):
    """Take what the base does not say (brightness, colour) from what the light shows.

    A base recorded while the light was off, or set by a bare turn_on, has no
    brightness; without one, the light would stay at a layer's after it goes off.
    """
    base = state["layers"][BASE]["attrs"]
    for group in (BRIGHTNESS, COLOUR):
        if not any(k in base for k in group):
            base.update({k: v for k, v in attrs.items() if k in group})


def by_hand(state, on, attrs):
    """The real light was changed by hand: that is the new base, the layers above go off."""
    for key, layer in state["layers"].items():
        if key != BASE:
            layer["on"] = False
    base = state["layers"][BASE]
    if not on:
        base["on"] = False
        return
    kept = {k: v for k, v in base["attrs"].items() if k != "effect"}  # shown, or none
    base["on"], base["attrs"] = True, _merge(kept, attrs)


def top(state):
    """The layer shown now: the highest that is on, else the base."""
    above = [(layer["priority"], layer["on_at"], key)
             for key, layer in state["layers"].items() if key != BASE and layer["on"]]  # fmt: skip
    return max(above)[2] if above else BASE


def target(state):
    """What the real light should show: {"on": bool, "attrs": {...}}."""
    base = state["layers"][BASE]
    key = top(state)
    if key == BASE:
        return {"on": base["on"], "attrs": dict(base["attrs"])}
    return {"on": True, "attrs": _merge(base["attrs"], state["layers"][key]["attrs"])}


def step(state):
    """The service call that brings the real light to its target, or None."""
    want = target(state)
    if want == state["applied"]:
        return None
    state["applied"] = want
    if not want["on"]:
        return ("turn_off", {})
    return ("turn_on", dict(want["attrs"]))
