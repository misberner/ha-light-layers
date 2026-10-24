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
LAYERED = "light.real_base"


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


DOOR = "light.real_door"
LAYERS = [{"id": "d00r", "name": "Door", "priority": 10}]


async def setup(hass, real_on=True, brightness=40, layers=LAYERS, options=None):
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
    options = options or {"wall_switch": False, "layers": layers}
    entry = MockConfigEntry(
        domain=DOMAIN, title="Porch", data={"light": REAL}, entry_id="porch", options=options
    )
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
    assert hass.states.get(DOOR).state == "off"
    await call(hass, "turn_on", LAYERED, brightness=100)
    assert bright(hass, REAL) == ("on", 100)
    await call(hass, "turn_off", LAYERED)
    assert hass.states.get(REAL).state == "off"


async def test_schedule_while_a_layer_is_on_is_shown_afterwards(hass, clock):
    """Door at 22:58 brightens; the 23:00 schedule dims; at 23:03 the light is dim."""
    await setup(hass, brightness=15)
    await call(hass, "turn_on", DOOR, brightness=255)
    assert bright(hass, REAL) == ("on", 255)
    assert hass.states.get(LAYERED).attributes["showing"] == "Door"
    assert hass.states.get(DOOR).attributes["showing"] is True
    await advance(hass, clock, 2)
    await call(hass, "turn_on", LAYERED, brightness=2)
    assert bright(hass, REAL) == ("on", 255)  # recorded, not shown
    assert bright(hass, LAYERED) == ("on", 2)  # the base shows the normal state
    await call(hass, "turn_off", DOOR)
    assert bright(hass, REAL) == ("on", 2)
    assert hass.states.get(LAYERED).attributes["showing"] == "base"


async def test_base_switched_off_under_a_layer_goes_off_afterwards(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "turn_on", DOOR, brightness=255)
    await call(hass, "turn_off", LAYERED)
    assert hass.states.get(REAL).state == "on"
    await call(hass, "turn_off", DOOR)
    assert hass.states.get(REAL).state == "off"


async def test_a_layer_on_while_the_base_is_off(hass, clock):
    await setup(hass, real_on=False)
    await call(hass, "turn_on", DOOR, brightness=255)
    assert bright(hass, REAL) == ("on", 255)
    await call(hass, "turn_off", DOOR)
    assert hass.states.get(REAL).state == "off"


async def test_priority(hass, clock):
    layers = [*LAYERS, {"id": "c1n0", "name": "Cinema", "priority": 20}]
    await setup(hass, brightness=15, layers=layers)
    await call(hass, "turn_on", "light.real_cinema", brightness=3)
    await call(hass, "turn_on", DOOR, brightness=255)
    assert bright(hass, REAL) == ("on", 3)
    await call(hass, "turn_off", "light.real_cinema")
    assert bright(hass, REAL) == ("on", 255)


async def test_change_at_the_real_light_becomes_the_base_and_turns_the_layers_off(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "turn_on", DOOR, brightness=255)
    await advance(hass, clock, 1)
    await call(hass, "turn_off", REAL)  # someone at the wall switch
    assert hass.states.get(LAYERED).state == "off"
    assert hass.states.get(DOOR).state == "off"
    assert hass.states.get(REAL).state == "off"


async def test_our_own_changes_are_not_taken_as_by_hand(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "turn_on", DOOR, brightness=153)
    # a late report of what we sent, with a context of its own (as Zigbee does)
    await advance(hass, clock, 0.5)
    hass.states.async_set(REAL, "on", {**hass.states.get(REAL).attributes, "brightness": 152},
                          context=Context())  # fmt: skip
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == "on"


async def test_back_from_unavailable_shows_the_target(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "turn_on", DOOR, brightness=153)
    attrs = dict(hass.states.get(REAL).attributes)
    hass.states.async_set(REAL, "unavailable", attrs)
    await hass.async_block_till_done()
    assert hass.states.get(LAYERED).state == "unavailable"
    assert hass.states.get(DOOR).state == "unavailable"
    hass.states.async_set(REAL, "off", attrs)  # power came back, the light is off
    await hass.async_block_till_done()
    assert bright(hass, REAL) == ("on", 153)


async def test_restart_restores_the_layers(hass, clock, hass_storage):
    hass_storage["light_layers.porch"] = {
        "version": 1,
        "key": "light_layers.porch",
        "data": {"layers": {
            "base": {"on": True, "attrs": {"brightness": 2}, "priority": 0, "on_at": 0},
            "d00r": {"on": True, "attrs": {"brightness": 153}, "priority": 10, "on_at": 1},
        }},
    }  # fmt: skip
    await setup(hass, real_on=False)
    assert bright(hass, REAL) == ("on", 153)  # the door layer is still on
    assert bright(hass, LAYERED) == ("on", 2)
    await call(hass, "turn_off", DOOR)
    assert bright(hass, REAL) == ("on", 2)


async def test_state_is_saved(hass, clock, hass_storage):
    entry = await setup(hass, brightness=15)
    await call(hass, "turn_on", DOOR, brightness=200)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    saved = hass_storage["light_layers.porch"]["data"]["layers"]
    assert saved["d00r"]["on"] is True and saved["d00r"]["attrs"] == {"brightness": 200}


async def test_config_flow(hass):
    assert await async_setup_component(
        hass, "template",
        {"template": [{"light": [{"name": "Real", "turn_on": [], "turn_off": []}]}]},
    )  # fmt: skip
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"light": REAL, "layer": "Door"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Real"  # the real light's name
    assert result["data"] == {"light": REAL}
    assert result["options"]["layers"][0]["name"] == "Door"
    assert result["options"]["layers"][0]["priority"] == 10
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


async def test_options_add_and_remove_a_layer(hass, clock):
    from homeassistant.helpers import entity_registry as er

    entry = await setup(hass, layers=[])
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] == "menu" and "remove_layer" not in flow["menu_options"]
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "add_layer"}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"name": "Door", "priority": 10}
    )
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == "off"
    await call(hass, "turn_on", DOOR, brightness=255)
    assert bright(hass, REAL) == ("on", 255)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "add_layer"}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"name": "door", "priority": 5}
    )
    assert flow["errors"] == {"name": "duplicate"}

    layer_id = entry.options["layers"][0]["id"]
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "remove_layer"}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"layers": [layer_id]}
    )
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(DOOR) is None
    assert bright(hass, REAL) == ("on", 40)  # the base again


async def test_automation_on_the_real_light_means_the_real_light(hass, clock):
    """E.g. an automation for a button at the light: like a change by hand."""
    await setup(hass, brightness=15)
    await call(hass, "turn_on", DOOR, brightness=153)
    await advance(hass, clock, 1)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": REAL}, blocking=True,
        context=Context(parent_id="button-automation-run"),
    )  # fmt: skip
    await hass.async_block_till_done()
    assert hass.states.get(REAL).state == "off"
    assert hass.states.get(LAYERED).state == "off"
    assert hass.states.get(DOOR).state == "off"


async def test_bare_turn_on_learns_the_brightness_to_return_to(hass, clock):
    """A base without a brightness would stay at a layer's after it goes off."""
    await setup(hass, real_on=False)
    await call(hass, "turn_on", LAYERED)  # no brightness: the light picks one
    await call(hass, "turn_on", REAL, brightness=30)  # (the real light reports it)
    assert hass.states.get(LAYERED).state == "on"
    await advance(hass, clock, 1)
    await call(hass, "turn_on", DOOR, brightness=153)
    await call(hass, "turn_off", DOOR)
    assert bright(hass, REAL) == ("on", 30)


async def test_real_light_loaded_later_is_taken_as_it_is(hass, clock):
    """Nothing to restore and the real light not there yet: do not switch it off."""
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", data={"light": REAL},
                            options={"layers": LAYERS})  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LAYERED).state == "unavailable"
    hass.states.async_set(REAL, "on", {"brightness": 80, "supported_color_modes": ["brightness"],
                                       "color_mode": "brightness"})  # fmt: skip
    await hass.async_block_till_done()
    assert bright(hass, LAYERED) == ("on", 80)
    assert hass.states.get(DOOR).state == "off"
    assert hass.states.get(REAL).state == "on"


async def test_layers_sit_on_the_real_lights_device(hass, clock):
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    other = MockConfigEntry(domain="test")
    other.add_to_hass(hass)
    area = ar.async_get(hass).async_create("Einfahrt")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("test", "porch")}, name="Front lamp"
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("light", "test", "real", suggested_object_id="real",
                                device_id=device.id)  # fmt: skip
    hass.states.async_set(REAL, "off", {"supported_color_modes": ["onoff"]})
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", data={"light": REAL},
                            options={"layers": LAYERS})  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # on the device, and named as the layers (not prefixed with the device name)
    assert ent_reg.async_get(LAYERED).device_id == device.id
    assert ent_reg.async_get(DOOR).device_id == device.id
    # all layers are hidden: area targets and counts reach the real light only
    assert ent_reg.async_get(LAYERED).hidden_by == er.RegistryEntryHider.INTEGRATION
    assert ent_reg.async_get(DOOR).hidden_by == er.RegistryEntryHider.INTEGRATION
    await hass.services.async_call("light", "turn_on", {"area_id": area.id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(LAYERED).state == "off"
    assert hass.states.get(DOOR).state == "off"


def report(hass, **attrs):
    """The real light reports something by itself (its own context)."""
    state = attrs.pop("state", "on")
    attrs = {**hass.states.get(REAL).attributes, **attrs}
    hass.states.async_set(REAL, state, attrs, context=Context())


async def test_colour_changed_at_the_light_is_by_hand(hass, clock):
    await setup(hass, brightness=40)
    await call(hass, "turn_on", LAYERED, color_temp_kelvin=2700)
    await call(hass, "turn_on", DOOR, brightness=153)
    await advance(hass, clock, 1)
    report(hass, color_mode="color_temp", color_temp_kelvin=4000)  # the vendor app
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == "off"
    assert hass.states.get(LAYERED).attributes["color_temp_kelvin"] == 4000


async def test_the_lights_own_rendition_is_not_by_hand(hass, clock):
    """A light that clamps or rounds what we sent keeps showing it that way."""
    await setup(hass, brightness=40)
    await call(hass, "turn_on", DOOR, color_temp_kelvin=2700)
    report(hass, color_mode="color_temp", color_temp_kelvin=3000)  # while settling
    await hass.async_block_till_done()
    await advance(hass, clock, 1)
    report(hass, color_mode="color_temp", color_temp_kelvin=3000, linkquality=90)
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == "on"


async def test_unchanged_report_is_not_by_hand(hass, clock):
    await setup(hass, brightness=15)
    await call(hass, "turn_on", DOOR, brightness=153)
    await advance(hass, clock, 1)
    report(hass, brightness=154)  # within a few steps
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == "on"


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
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", data={"light": REAL},
                            options={"layers": LAYERS})  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_effect_is_stopped_when_the_layer_goes_off(hass, clock):
    from homeassistant.const import EVENT_CALL_SERVICE
    from pytest_homeassistant_custom_component.common import async_capture_events

    await _setup_effects(hass)
    calls = async_capture_events(hass, EVENT_CALL_SERVICE)
    await call(hass, "turn_on", DOOR, effect="candle")
    await call(hass, "turn_off", DOOR)
    sent = [
        c.data["service_data"]
        for c in calls
        if c.data["domain"] == "light" and c.data["service_data"].get("entity_id") == REAL
    ]
    assert sent[0]["effect"] == "candle"
    assert sent[-1]["effect"] == "off" and sent[-1]["brightness"] == 40


async def test_effect_changed_at_the_light_is_by_hand(hass, clock):
    await _setup_effects(hass)
    await call(hass, "turn_on", DOOR, brightness=153)
    await advance(hass, clock, 1)
    report(hass, effect="candle")
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == "off"
    assert hass.states.get(LAYERED).attributes["effect"] == "candle"


async def test_renamed_real_light_is_followed(hass, clock):
    from homeassistant.helpers import entity_registry as er

    assert await async_setup_component(
        hass, "template",
        {"template": [{"light": [{"name": "Real", "unique_id": "real",
                                  "turn_on": [], "turn_off": []}]}]},
    )  # fmt: skip
    await hass.async_block_till_done()
    entry = MockConfigEntry(domain=DOMAIN, title="Porch", data={"light": REAL}, unique_id=REAL,
                            options={"layers": LAYERS})  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    er.async_get(hass).async_update_entity(REAL, new_entity_id="light.real_raw")
    await hass.async_block_till_done()
    assert entry.data["light"] == "light.real_raw" and entry.unique_id == "light.real_raw"
    await call(hass, "turn_on", DOOR)  # the layers keep their entity ids
    assert hass.states.get("light.real_raw").state == "on"


async def test_wall_switch_back_on_is_by_hand(hass, clock):
    await setup(hass, real_on=False, options={"wall_switch": True, "layers": LAYERS})
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
