"""The layered light in front of a real (template) light."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import Context
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.light_layers import light as layered
from custom_components.light_layers.const import DOMAIN

REAL = "light.real"
LAYERED = "light.porch"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class Clock:
    """Drives both the integration's minutes and HA's timers."""

    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t


@pytest.fixture
def clock():
    c = Clock()
    with patch.object(layered, "_seconds", c.time):
        yield c


async def advance(hass, clock, minutes):
    clock.t += minutes * 60
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=minutes, seconds=1))
    await hass.async_block_till_done()


async def setup(hass, real_on=True, brightness=40):
    assert await async_setup_component(
        hass,
        "template",
        {
            "template": [
                {
                    "light": [
                        {
                            "name": "Real",
                            "turn_on": [],
                            "turn_off": [],
                            "set_level": [],
                            "set_temperature": [],
                        }
                    ]
                }
            ]
        },  # fmt: skip
    )
    await hass.async_block_till_done()
    if real_on:
        await call(hass, "turn_on", REAL, brightness=brightness)
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", data={"light": REAL},
                            options={"minutes": 5})  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def call(hass, service, entity, domain="light", **data):
    await hass.services.async_call(domain, service, {"entity_id": entity, **data}, blocking=True)
    await hass.async_block_till_done()


def bright(hass, entity):
    st = hass.states.get(entity)
    return st.state, st.attributes.get("brightness")


async def test_starts_from_the_real_light_and_passes_commands_through(hass, clock):
    await setup(hass)
    assert bright(hass, LAYERED) == ("on", 40)
    await call(hass, "turn_on", LAYERED, brightness=100)
    assert bright(hass, REAL) == ("on", 100)
    await call(hass, "turn_off", LAYERED)
    assert hass.states.get(REAL).state == "off"


async def test_schedule_during_override_is_shown_afterwards(hass, clock):
    """Door at 22:58 brightens; the 23:00 schedule dims; at 23:03 the light is dim."""
    await setup(hass, brightness=15)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    assert bright(hass, REAL) == ("on", 153)
    assert hass.states.get(LAYERED).attributes["showing"] == "override door"
    await advance(hass, clock, 2)
    await call(hass, "turn_on", LAYERED, brightness=2)
    assert bright(hass, REAL) == ("on", 153)  # recorded, not shown
    assert bright(hass, LAYERED) == ("on", 2)  # the stand-in shows the normal state
    await advance(hass, clock, 3.5)
    assert bright(hass, REAL) == ("on", 2)
    assert hass.states.get(LAYERED).attributes["showing"] == "base"


async def test_switched_off_during_override_goes_off_afterwards(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await advance(hass, clock, 1)
    await call(hass, "turn_off", LAYERED)
    assert hass.states.get(REAL).state == "on"
    await advance(hass, clock, 4.5)
    assert hass.states.get(REAL).state == "off"


async def test_override_again_extends(hass, clock):
    await setup(hass, real_on=False)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await advance(hass, clock, 4)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await advance(hass, clock, 4)
    assert hass.states.get(REAL).state == "on"
    await advance(hass, clock, 1.5)
    assert hass.states.get(REAL).state == "off"


async def test_cancel(hass, clock):
    await setup(hass, real_on=False)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60, minutes=30)
    await call(hass, "cancel", LAYERED, domain=DOMAIN, key="door")
    assert hass.states.get(REAL).state == "off"


async def test_change_at_the_real_light_ends_overrides_and_becomes_the_base(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await advance(hass, clock, 1)
    # someone at the wall switch, not through the stand-in
    await call(hass, "turn_off", REAL)
    assert hass.states.get(LAYERED).state == "off"
    assert hass.states.get(LAYERED).attributes["overrides"] == {}
    await advance(hass, clock, 5)
    assert hass.states.get(REAL).state == "off"


async def test_our_own_changes_are_not_taken_as_by_hand(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    # a late report of what we sent, with a context of its own (as Zigbee does)
    await advance(hass, clock, 0.5)
    hass.states.async_set(REAL, "on", {**hass.states.get(REAL).attributes, "brightness": 152},
                          context=Context())  # fmt: skip
    await hass.async_block_till_done()
    assert hass.states.get(LAYERED).attributes["showing"] == "override door"


async def test_off_override_and_priority(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="dark", off=True, priority=5,
               minutes=1)  # fmt: skip
    assert hass.states.get(REAL).state == "off"
    await advance(hass, clock, 1.5)
    assert bright(hass, REAL) == ("on", 153)


async def test_back_from_unavailable_shows_the_target(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    attrs = dict(hass.states.get(REAL).attributes)
    hass.states.async_set(REAL, "unavailable", attrs)
    await hass.async_block_till_done()
    assert hass.states.get(LAYERED).state == "unavailable"
    hass.states.async_set(REAL, "off", attrs)  # power came back, the light is off
    await hass.async_block_till_done()
    assert bright(hass, REAL) == ("on", 153)


async def test_restart_restores_base_and_override(hass, clock):
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

    layer = {"base": {"on": True, "attrs": {"brightness": 2}},
             "overrides": {"door": {"attrs": {"brightness": 153}, "until": clock.t / 60 + 2,
                                    "priority": 0, "set_at": clock.t / 60}},
             "applied": None}  # fmt: skip
    mock_restore_cache_with_extra_data(hass, [(State(LAYERED, "on"), {"layer": layer})])
    await setup(hass, real_on=False)
    assert bright(hass, REAL) == ("on", 153)  # the override is still running
    await advance(hass, clock, 2.5)
    assert bright(hass, REAL) == ("on", 2)


async def test_config_flow(hass):
    assert await async_setup_component(
        hass, "template",
        {"template": [{"light": [{"name": "Real", "turn_on": [], "turn_off": []}]}]},
    )  # fmt: skip
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Porch", "light": REAL, "minutes": 3}
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {"light": REAL}
    assert result["options"] == {"minutes": 3, "wall_switch": False}
    await hass.async_block_till_done()
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    again = await hass.config_entries.flow.async_configure(
        again["flow_id"], {"name": "Porch 2", "light": REAL}
    )
    assert again["type"] == "abort"
    layered_flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    layered_flow = await hass.config_entries.flow.async_configure(
        layered_flow["flow_id"], {"name": "Loop", "light": LAYERED}
    )
    assert layered_flow["errors"] == {"light": "layered"}


async def test_automation_on_the_real_light_means_the_real_light(hass, clock):
    """E.g. an automation for a button at the light: like a change by hand."""
    await setup(hass, brightness=15)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await advance(hass, clock, 1)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": REAL}, blocking=True,
        context=Context(parent_id="button-automation-run"),
    )  # fmt: skip
    await hass.async_block_till_done()
    assert hass.states.get(REAL).state == "off"
    assert hass.states.get(LAYERED).state == "off"
    assert hass.states.get(LAYERED).attributes["overrides"] == {}


async def test_bare_turn_on_learns_the_brightness_an_override_returns_to(hass, clock):
    """A base without a brightness would come back at the override's."""
    await setup(hass, real_on=False)
    await call(hass, "turn_on", LAYERED)  # no brightness: the light picks one
    await call(hass, "turn_on", REAL, brightness=30)  # (the real light reports it)
    assert hass.states.get(LAYERED).attributes["overrides"] == {}
    await advance(hass, clock, 1)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await advance(hass, clock, 5.5)
    assert bright(hass, REAL) == ("on", 30)


async def test_real_light_loaded_later_is_taken_as_it_is(hass, clock):
    """Nothing to restore and the real light not there yet: do not switch it off."""
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", data={"light": REAL},
                            options={"minutes": 5})  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LAYERED).state == "unavailable"
    hass.states.async_set(REAL, "on", {"brightness": 80, "supported_color_modes": ["brightness"],
                                       "color_mode": "brightness"})  # fmt: skip
    await hass.async_block_till_done()
    assert bright(hass, LAYERED) == ("on", 80)
    assert hass.states.get(REAL).state == "on"


async def test_stand_in_sits_on_the_real_lights_device(hass, clock):
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    other = MockConfigEntry(domain="test")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("test", "porch")}
    )
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("light", "test", "real", suggested_object_id="real",
                                device_id=device.id)  # fmt: skip
    hass.states.async_set(REAL, "off", {"supported_color_modes": ["onoff"]})
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", data={"light": REAL})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ent_reg.async_get(LAYERED).device_id == device.id


def report(hass, **attrs):
    """The real light reports something by itself (its own context)."""
    state = attrs.pop("state", "on")
    attrs = {**hass.states.get(REAL).attributes, **attrs}
    hass.states.async_set(REAL, state, attrs, context=Context())


async def test_colour_changed_at_the_light_is_by_hand(hass, clock):
    await setup(hass, brightness=40)
    await call(hass, "turn_on", LAYERED, color_temp_kelvin=2700)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await advance(hass, clock, 1)
    report(hass, color_mode="color_temp", color_temp_kelvin=4000)  # the vendor app
    await hass.async_block_till_done()
    st = hass.states.get(LAYERED)
    assert st.attributes["overrides"] == {}
    assert st.attributes["color_temp_kelvin"] == 4000


async def test_the_lights_own_rendition_is_not_by_hand(hass, clock):
    """A light that clamps or rounds what we sent keeps showing it that way."""
    await setup(hass, brightness=40)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", color_temp_kelvin=2700)
    report(hass, color_mode="color_temp", color_temp_kelvin=3000)  # while settling
    await hass.async_block_till_done()
    await advance(hass, clock, 1)
    report(hass, color_mode="color_temp", color_temp_kelvin=3000, linkquality=90)
    await hass.async_block_till_done()
    assert hass.states.get(LAYERED).attributes["showing"] == "override door"


async def test_unchanged_report_is_not_by_hand(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await advance(hass, clock, 1)
    report(hass, brightness=154)  # within a few steps
    await hass.async_block_till_done()
    assert hass.states.get(LAYERED).attributes["showing"] == "override door"


async def _setup_effects(hass):
    assert await async_setup_component(
        hass, "template",
        {"template": [{"light": [{
            "name": "Real", "turn_on": [], "turn_off": [], "set_level": [],
            "effect_list": "{{ ['off', 'candle'] }}", "effect": "{{ 'off' }}", "set_effect": [],
        }]}]},
    )  # fmt: skip
    await hass.async_block_till_done()
    await call(hass, "turn_on", REAL, brightness=40)
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", data={"light": REAL})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_effect_is_stopped_when_the_override_ends(hass, clock):
    from homeassistant.const import EVENT_CALL_SERVICE
    from pytest_homeassistant_custom_component.common import async_capture_events

    await _setup_effects(hass)
    calls = async_capture_events(hass, EVENT_CALL_SERVICE)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="party", effect="candle", minutes=1)
    await advance(hass, clock, 1.5)
    sent = [
        c.data["service_data"]
        for c in calls
        if c.data["domain"] == "light" and c.data["service_data"].get("entity_id") == REAL
    ]
    assert sent[0]["effect"] == "candle"
    assert sent[-1]["effect"] == "off" and sent[-1]["brightness"] == 40


async def test_effect_changed_at_the_light_is_by_hand(hass, clock):
    await _setup_effects(hass)
    await call(hass, "override", LAYERED, domain=DOMAIN, key="door", brightness_pct=60)
    await advance(hass, clock, 1)
    report(hass, effect="candle")
    await hass.async_block_till_done()
    st = hass.states.get(LAYERED)
    assert st.attributes["overrides"] == {} and st.attributes["effect"] == "candle"


async def test_renamed_real_light_is_followed(hass, clock):
    from homeassistant.helpers import entity_registry as er

    assert await async_setup_component(
        hass, "template",
        {"template": [{"light": [{"name": "Real", "unique_id": "real",
                                  "turn_on": [], "turn_off": []}]}]},
    )  # fmt: skip
    await hass.async_block_till_done()
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", data={"light": REAL}, unique_id=REAL)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    er.async_get(hass).async_update_entity(REAL, new_entity_id="light.real_raw")
    await hass.async_block_till_done()
    assert entry.data["light"] == "light.real_raw" and entry.unique_id == "light.real_raw"
    await call(hass, "turn_on", LAYERED)
    assert hass.states.get("light.real_raw").state == "on"


async def test_wall_switch_back_on_is_by_hand(hass, clock):
    await setup(hass, real_on=False)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(entry, options={"minutes": 5, "wall_switch": True})
    await hass.async_block_till_done()
    attrs = dict(hass.states.get(REAL).attributes)
    hass.states.async_set(REAL, "unavailable", attrs)  # switched off at the wall
    await hass.async_block_till_done()
    await advance(hass, clock, 1)
    hass.states.async_set(REAL, "on", {**attrs, "brightness": 255})  # and on again
    await hass.async_block_till_done()
    assert bright(hass, LAYERED) == ("on", 255)
    assert hass.states.get(REAL).state == "on"


async def test_without_wall_switch_back_on_shows_the_normal_state(hass, clock):
    await setup(hass, real_on=False)
    attrs = dict(hass.states.get(REAL).attributes)
    hass.states.async_set(REAL, "unavailable", attrs)  # a power cut
    await hass.async_block_till_done()
    hass.states.async_set(REAL, "on", {**attrs, "brightness": 255})
    await hass.async_block_till_done()
    assert hass.states.get(REAL).state == "off"
