"""Temporary light overrides over a recorded base (custom_components/light_layers/logic.py)."""

import pytest

from custom_components.light_layers import logic as ll


def test_config_rejects_unknown_settings():
    with pytest.raises(KeyError):
        ll.config({"override_minuts": 3})


def test_without_override_commands_pass_through():
    layer = ll.new_layer()
    ll.command(layer, "turn_on", {"brightness": 15})
    assert ll.step(layer, 0) == ("turn_on", {"brightness": 15})
    assert ll.step(layer, 1) is None  # nothing new to send
    ll.command(layer, "turn_off")
    assert ll.step(layer, 2) == ("turn_off", {})


def test_schedule_dims_during_override_light_follows_afterwards():
    """22:58 door, 23:00 the schedule dims to 2: bright until 23:03, then 2."""
    layer = ll.new_layer(on=True, attrs={"brightness": 15, "color_temp_kelvin": 2700})
    ll.step(layer, 0)
    ll.override(layer, "door", {"brightness_pct": 60}, 5, now=0)
    assert ll.step(layer, 0) == ("turn_on", {"brightness_pct": 60, "color_temp_kelvin": 2700})
    ll.command(layer, "turn_on", {"brightness": 2})
    assert ll.step(layer, 2) is None  # recorded, not shown
    assert ll.step(layer, 5) == ("turn_on", {"brightness": 2, "color_temp_kelvin": 2700})


def test_switch_off_during_override_light_goes_off_afterwards():
    layer = ll.new_layer(on=True, attrs={"brightness": 15})
    ll.step(layer, 0)
    ll.override(layer, "door", {"brightness_pct": 60}, 5, now=0)
    ll.step(layer, 0)
    ll.command(layer, "turn_off")
    assert ll.step(layer, 1) is None
    assert ll.step(layer, 5) == ("turn_off", {})


def test_override_turns_an_off_light_on():
    layer = ll.new_layer(on=False, attrs={"brightness": 15})
    ll.step(layer, 0)
    ll.override(layer, "door", {"brightness_pct": 60}, 5, now=0)
    assert ll.step(layer, 0) == ("turn_on", {"brightness_pct": 60})
    assert ll.step(layer, 5) == ("turn_off", {})


def test_again_extends_never_shortens():
    layer = ll.new_layer()
    ll.override(layer, "door", {"brightness_pct": 60}, 5, now=0)
    ll.override(layer, "door", {"brightness_pct": 60}, 5, now=3)
    assert ll.next_end(layer) == 8
    ll.override(layer, "door", {"brightness_pct": 60}, 1, now=4)
    assert ll.next_end(layer) == 8


def test_higher_priority_wins_and_the_next_takes_over():
    layer = ll.new_layer(on=True, attrs={"brightness": 15})
    ll.override(layer, "door", {"brightness_pct": 60}, 5, now=0)
    ll.override(layer, "bell", {"brightness_pct": 100}, 2, now=1, priority=10)
    assert ll.target(layer, 1)["attrs"]["brightness_pct"] == 100
    assert ll.target(layer, 3)["attrs"]["brightness_pct"] == 60
    assert ll.target(layer, 5)["attrs"] == {"brightness": 15}


def test_by_hand_ends_overrides_and_becomes_the_base():
    layer = ll.new_layer(on=True, attrs={"brightness": 15})
    ll.override(layer, "door", {"brightness_pct": 60}, 5, now=0)
    ll.step(layer, 0)
    ll.by_hand(layer, False, {})
    assert ll.active(layer, 1) is None
    assert ll.target(layer, 1) == {"on": False, "attrs": {"brightness": 15}}


def test_colour_replaces_colour_brightness_replaces_brightness():
    layer = ll.new_layer(on=True, attrs={"brightness": 15, "xy_color": [0.5, 0.4]})
    ll.command(layer, "turn_on", {"color_temp_kelvin": 2700, "brightness_pct": 40})
    assert layer["base"]["attrs"] == {"color_temp_kelvin": 2700, "brightness_pct": 40}


def test_toggle_and_override_off():
    layer = ll.new_layer(on=True)
    ll.command(layer, "toggle")
    assert layer["base"]["on"] is False
    ll.command(layer, "turn_on")
    ll.override(layer, "night", {"on": False}, 5, now=0)
    assert ll.target(layer, 0) == {"on": False, "attrs": {}}


def test_fill_takes_only_what_the_base_does_not_say():
    layer = ll.new_layer(on=True, attrs={"color_temp_kelvin": 2700})
    ll.fill(layer, {"brightness": 40, "xy_color": [0.5, 0.4]})
    assert layer["base"]["attrs"] == {"color_temp_kelvin": 2700, "brightness": 40}
