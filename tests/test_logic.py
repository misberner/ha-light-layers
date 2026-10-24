"""Layers over a base (custom_components/light_layers/logic.py)."""

import pytest

from custom_components.light_layers import logic

DOOR, DARKEN = "door", "darken"


def state(on=True, brightness=15, layers=None):
    st = logic.new_state(on, {"brightness": brightness} if on else {})
    logic.configure(st, layers or {DOOR: 10})
    return st


def test_base_alone_is_shown():
    st = state()
    assert logic.target(st) == {"on": True, "attrs": {"brightness": 15}}
    assert logic.top(st) == logic.BASE


def test_a_layer_on_shows_itself_over_the_base():
    st = state()
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=1)
    assert logic.top(st) == DOOR
    assert logic.target(st) == {"on": True, "attrs": {"brightness": 255}}


def test_commands_to_the_base_while_covered_show_once_the_layer_goes_off():
    """Door at 22:58 brightens; the 23:00 schedule dims; the door layer off at 23:03."""
    st = state()
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=1)
    logic.command(st, logic.BASE, "turn_on", {"brightness": 2}, now=2)
    assert logic.target(st)["attrs"] == {"brightness": 255}
    logic.command(st, DOOR, "turn_off", now=5)
    assert logic.target(st) == {"on": True, "attrs": {"brightness": 2}}


def test_base_off_during_a_layer_goes_off_afterwards():
    st = state()
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=1)
    logic.command(st, logic.BASE, "turn_off", now=2)
    assert logic.target(st)["on"] is True
    logic.command(st, DOOR, "turn_off", now=3)
    assert logic.target(st)["on"] is False and logic.step(st) == ("turn_off", {})


def test_a_layer_takes_what_it_leaves_open_from_the_base():
    st = state()
    logic.command(st, logic.BASE, "turn_on", {"color_temp_kelvin": 2700}, now=0)
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=1)
    assert logic.target(st)["attrs"] == {"brightness": 255, "color_temp_kelvin": 2700}


def test_highest_priority_wins_and_the_next_takes_over():
    st = state(layers={DOOR: 10, DARKEN: 20})
    logic.command(st, DARKEN, "turn_on", {"brightness": 5}, now=1)
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=2)
    assert logic.top(st) == DARKEN
    logic.command(st, DARKEN, "turn_off", now=3)
    assert logic.top(st) == DOOR


def test_equal_priority_the_last_switched_on_wins():
    st = state(layers={DOOR: 10, DARKEN: 10})
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=1)
    logic.command(st, DARKEN, "turn_on", {"brightness": 5}, now=2)
    assert logic.top(st) == DARKEN
    logic.command(st, DOOR, "turn_on", {"brightness": 200}, now=3)  # already on: no change
    assert logic.top(st) == DARKEN


def test_a_layer_keeps_its_settings_while_off():
    st = state()
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=1)
    logic.command(st, DOOR, "turn_off", now=2)
    logic.command(st, DOOR, "turn_on", now=3)
    assert logic.target(st)["attrs"] == {"brightness": 255}


def test_by_hand_becomes_the_base_and_turns_the_layers_off():
    st = state()
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=1)
    logic.by_hand(st, True, {"brightness": 80})
    assert logic.top(st) == logic.BASE
    assert st["layers"][DOOR]["on"] is False
    assert logic.target(st) == {"on": True, "attrs": {"brightness": 80}}
    logic.by_hand(st, False, {})
    assert logic.target(st)["on"] is False


def test_step_sends_only_changes():
    st = state()
    assert logic.step(st) == ("turn_on", {"brightness": 15})
    assert logic.step(st) is None
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=1)
    assert logic.step(st) == ("turn_on", {"brightness": 255})


def test_configure_adds_and_removes_layers():
    st = state(layers={DOOR: 10})
    logic.command(st, DOOR, "turn_on", {"brightness": 255}, now=1)
    logic.configure(st, {DARKEN: 5})
    assert set(st["layers"]) == {logic.BASE, DARKEN}
    assert st["layers"][DARKEN]["on"] is False and st["layers"][DARKEN]["priority"] == 5


def test_fill_takes_only_what_the_base_does_not_say():
    st = logic.new_state(True, {})
    logic.fill(st, {"brightness": 30, "color_temp_kelvin": 3000})
    assert st["layers"][logic.BASE]["attrs"] == {"brightness": 30, "color_temp_kelvin": 3000}
    logic.fill(st, {"brightness": 99})
    assert st["layers"][logic.BASE]["attrs"]["brightness"] == 30


def test_colour_replaces_colour_and_no_effect_is_dropped():
    st = state()
    logic.command(st, logic.BASE, "turn_on", {"hs_color": [10, 50]}, now=0)
    logic.command(st, logic.BASE, "turn_on", {"color_temp_kelvin": 3000, "effect": "off"}, now=0)
    assert st["layers"][logic.BASE]["attrs"] == {"brightness": 15, "color_temp_kelvin": 3000}


def test_config_rejects_typos():
    with pytest.raises(KeyError):
        logic.config({"own_change_secs": 1})
