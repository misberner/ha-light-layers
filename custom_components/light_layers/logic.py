"""A light's normal state, with temporary overrides on top (the layered light entity).

Every ordinary command (schedules, scenes, dashboards, voice) goes through
a stand-in light and sets the light's *base*: what it should be with no
override.  Overrides ("door opened at night: 60 % for 5 minutes") sit on
top with a priority and an end; the light shows the highest active one,
or the base.  Commands during an override are recorded in the base but not
shown, so when the override ends the light goes to the state it would be
in now, not back to how it was before.

A layer is a dict: base {on, attrs}, overrides {key: {attrs, until,
priority, set_at}}, applied (what was last sent to the real light).
"""

DEFAULTS = {
    "override_minutes": 5.0,
    "own_change_seconds": 10.0,  # real-light reports this soon after a command are ours
}

# attributes a light remembers between on and off; any one replaces the
# others of its kind (setting a colour temperature drops an xy colour)
COLOUR = ("color_temp_kelvin", "hs_color", "xy_color", "rgb_color", "rgbw_color", "rgbww_color")
BRIGHTNESS = ("brightness", "brightness_pct")
KEPT = BRIGHTNESS + COLOUR + ("effect",)


def config(overrides=None):
    cfg = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key not in DEFAULTS:
            raise KeyError(f"light_layers: unknown setting {key!r}")
        cfg[key] = value
    return cfg


def new_layer(on=False, attrs=None):
    return {"base": {"on": on, "attrs": dict(attrs or {})}, "overrides": {}, "applied": None}


def _merge(attrs, update):
    out = dict(attrs)
    for group in (BRIGHTNESS, COLOUR):
        if any(k in update for k in group):
            for k in group:
                out.pop(k, None)
    out.update({k: v for k, v in update.items() if k in KEPT and v is not None})
    return out


def command(layer, action, data=None):
    """An ordinary turn_on / turn_off / toggle: it sets the base."""
    base = layer["base"]
    if action == "toggle":
        action = "turn_off" if base["on"] else "turn_on"
    if action == "turn_off":
        base["on"] = False
    else:
        base["on"] = True
        base["attrs"] = _merge(base["attrs"], data or {})


def override(layer, key, attrs, minutes, now, priority=0):
    """Show *attrs* for *minutes* from *now* (minutes; again: extend, never shorten)."""
    previous = layer["overrides"].get(key)
    until = now + minutes
    if previous:
        until = max(until, previous["until"])
    layer["overrides"][key] = {"attrs": dict(attrs), "until": until, "priority": priority,
                               "set_at": now}  # fmt: skip


def cancel(layer, key=None):
    if key is None:
        layer["overrides"].clear()
    else:
        layer["overrides"].pop(key, None)


def by_hand(layer, on, attrs):
    """The real light was changed by hand: that is the new base, overrides end."""
    layer["overrides"].clear()
    layer["base"] = {"on": on, "attrs": _merge(layer["base"]["attrs"], attrs) if on else
                     layer["base"]["attrs"]}  # fmt: skip


def active(layer, now):
    """The override shown now, as (key, override), or None; drops ended ones."""
    for key in [k for k, o in layer["overrides"].items() if o["until"] <= now]:
        del layer["overrides"][key]
    if not layer["overrides"]:
        return None
    return max(layer["overrides"].items(), key=lambda kv: (kv[1]["priority"], kv[1]["set_at"]))


def target(layer, now):
    """What the real light should show: {"on": bool, "attrs": {...}}."""
    top = active(layer, now)
    if top is None:
        return {"on": layer["base"]["on"], "attrs": dict(layer["base"]["attrs"])}
    attrs = top[1]["attrs"]
    if attrs.get("on") is False:
        return {"on": False, "attrs": {}}
    return {"on": True, "attrs": _merge(layer["base"]["attrs"], attrs)}


def step(layer, now):
    """The service call that brings the real light to its target, or None."""
    want = target(layer, now)
    if want == layer["applied"]:
        return None
    layer["applied"] = want
    if not want["on"]:
        return ("turn_off", {})
    return ("turn_on", dict(want["attrs"]))


def next_end(layer):
    ends = [o["until"] for o in layer["overrides"].values()]
    return min(ends) if ends else None
