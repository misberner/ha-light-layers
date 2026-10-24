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


DOOR = "light.porch_door"
LAYERS = [{"id": "d00r", "name": "Door", "priority": 10}]


async def add_light(hass, layers=LAYERS, **data):
    """The integration's entry with one layered light (subentry "porch") for REAL."""
    sub = {"light": REAL, "wall_switch": False, "layers": layers, **data}
    entry = MockConfigEntry(
        domain=DOMAIN, title="Light Layers", data={}, version=2, entry_id="ll",
        subentries_data=[{"data": sub, "subentry_id": "porch", "subentry_type": "light",
                          "title": "Porch", "unique_id": sub["light"]}],
    )  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def setup(hass, real_on=True, brightness=40, layers=LAYERS, **data):
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
    return await add_light(hass, layers=layers, **data)


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
    await call(hass, "turn_on", "light.porch_cinema", brightness=3)
    await call(hass, "turn_on", DOOR, brightness=255)
    assert bright(hass, REAL) == ("on", 3)
    await call(hass, "turn_off", "light.porch_cinema")
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


async def test_config_flow_is_empty_and_single(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "create_entry" and result["data"] == {}
    await hass.async_block_till_done()
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert again["type"] == "abort"


async def _empty_entry(hass, light_unique_id=None):
    assert await async_setup_component(
        hass, "template",
        {"template": [{"light": [{
            "name": "Real", "turn_on": [], "turn_off": [], "set_level": [],
            **({"unique_id": light_unique_id} if light_unique_id else {}),
        }]}]},
    )  # fmt: skip
    await hass.async_block_till_done()
    entry = MockConfigEntry(domain=DOMAIN, title="Light Layers", data={}, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _add(hass, entry, **user_input):
    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "light"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], user_input)
    await hass.async_block_till_done()
    return result


async def test_add_a_light_without_taking_over(hass, clock):
    entry = await _empty_entry(hass)
    result = await _add(hass, entry, light=REAL, name="Porch", take_over=False, layer="Door")
    assert result["type"] == "create_entry"
    assert hass.states.get(LAYERED).state == "off"
    assert hass.states.get(DOOR).state == "off"
    assert hass.states.get(REAL) is not None  # the real light keeps its id
    again = await _add(hass, entry, light=REAL, take_over=False)
    assert again["type"] == "abort"
    loop = await _add(hass, entry, light=LAYERED, take_over=False)
    assert loop["errors"] == {"light": "layered"}


async def test_without_a_unique_id_the_light_cannot_be_taken_over(hass):
    entry = await _empty_entry(hass)
    result = await _add(hass, entry, light=REAL)
    assert result["errors"] == {"light": "not_in_registry"}


async def test_taking_over_and_giving_back(hass, clock):
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import label_registry as lr

    entry = await _empty_entry(hass, light_unique_id="real")
    registry = er.async_get(hass)
    label = lr.async_get(hass).async_create("Outdoor").label_id
    registry.async_update_entity(REAL, labels={label})
    registry.async_update_entity_options(REAL, "conversation", {"should_expose": True})
    result = await _add(hass, entry, light=REAL, layer="Door")
    assert result["type"] == "create_entry" and result["title"] == "Real"
    raw = registry.async_get("light.real_raw")
    assert raw is not None and raw.hidden_by == er.RegistryEntryHider.INTEGRATION
    assert raw.labels == set() and raw.options["conversation"]["should_expose"] is False
    base = registry.async_get(REAL)
    assert base.platform == DOMAIN and base.labels == {label}
    assert base.options["conversation"]["should_expose"] is True
    await call(hass, "turn_on", "light.real_door", brightness=255)
    assert bright(hass, "light.real_raw") == ("on", 255)

    sub_id = next(iter(entry.subentries))
    assert hass.config_entries.async_remove_subentry(entry, sub_id)
    await hass.async_block_till_done()
    back = registry.async_get(REAL)
    assert back.platform == "template" and back.hidden_by is None and back.labels == {label}
    assert back.options["conversation"]["should_expose"] is True
    assert registry.async_get("light.real_door") is None


async def _reconfigure(hass, entry, step, user_input):
    flow = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "light"), context={"source": "reconfigure", "subentry_id": "porch"}
    )
    assert flow["type"] == "menu"
    flow = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"next_step_id": step}
    )
    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], user_input)
    await hass.async_block_till_done()
    return result


async def test_reconfigure_adds_and_removes_layers(hass, clock):
    from homeassistant.helpers import entity_registry as er

    entry = await setup(hass, layers=[])
    result = await _reconfigure(hass, entry, "add_layer", {"name": "Door", "priority": 10})
    assert result["type"] == "abort"
    assert hass.states.get(DOOR).state == "off"
    await call(hass, "turn_on", DOOR, brightness=255)
    assert bright(hass, REAL) == ("on", 255)

    dup = await _reconfigure(hass, entry, "add_layer", {"name": "door", "priority": 5})
    assert dup["errors"] == {"name": "duplicate"}

    layer_id = entry.subentries["porch"].data["layers"][0]["id"]
    await _reconfigure(hass, entry, "remove_layer", {"layers": [layer_id]})
    assert er.async_get(hass).async_get(DOOR) is None
    assert bright(hass, REAL) == ("on", 40)  # the base again


async def test_reconfigure_settings(hass, clock):
    entry = await setup(hass)
    await _reconfigure(hass, entry, "settings", {"wall_switch": True})
    assert entry.subentries["porch"].data["wall_switch"] is True


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
    await add_light(hass)
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
    await add_light(hass)
    # on the device, and named as the layers (not prefixed with the device name)
    assert ent_reg.async_get(LAYERED).device_id == device.id
    assert ent_reg.async_get(DOOR).device_id == device.id
    # the layer above is hidden: an area target reaches only the base
    assert ent_reg.async_get(LAYERED).hidden_by is None
    assert ent_reg.async_get(DOOR).hidden_by == er.RegistryEntryHider.INTEGRATION
    await hass.services.async_call("light", "turn_on", {"area_id": area.id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(LAYERED).state == "on"
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
    await add_light(hass)


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
    entry = await add_light(hass)
    er.async_get(hass).async_update_entity(REAL, new_entity_id="light.real_raw")
    await hass.async_block_till_done()
    assert entry.subentries["porch"].data["light"] == "light.real_raw"
    await call(hass, "turn_on", DOOR)
    assert hass.states.get("light.real_raw").state == "on"


async def test_wall_switch_back_on_is_by_hand(hass, clock):
    await setup(hass, real_on=False, wall_switch=True)
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


async def test_removing_the_integration_gives_every_light_back(hass, clock):
    from homeassistant.helpers import entity_registry as er

    entry = await _empty_entry(hass, light_unique_id="real")
    await _add(hass, entry, light=REAL, layer="Door")
    assert er.async_get(hass).async_get(REAL).platform == DOMAIN
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    back = er.async_get(hass).async_get(REAL)
    assert back.platform == "template" and back.hidden_by is None
    assert er.async_get(hass).async_get("light.real_raw") is None
