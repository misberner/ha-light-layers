<img src="custom_components/light_layers/brand/icon.png" alt="" width="128" align="right">

# Light Layers

Layers for a Home Assistant light. Each layer is a light entity of its own;
the real light shows the highest layer that is on.

The usual "brighten for a few minutes, then restore" takes a snapshot and puts
it back. If a schedule changes the light in between, the snapshot is stale:
the light goes off immediately, or comes back at the old brightness for the
rest of the night. Here the schedule changes the base layer while a layer
above it is on; when that layer goes off, the light shows the base *as it is
then*.

## How it works

- **One helper per light:** Settings → Devices & services → Helpers → Create
  helper → Light Layers, pick the real light (and optionally a first layer).
  The real light keeps its entity id and stays what people and dashboards see.
- **Base:** `<light>_base` is the base layer, the light's normal state. Point
  schedules and scenes at it instead of the real light. It mirrors the real
  light's colour modes, colour temperature range, effects and transitions.
- **Layers:** more layers under the helper's *Options → Add a layer*: a name and
  a priority each. A layer is a light too (`<light>_<layer>`). While it is on,
  the real light shows it, with what it leaves open (a layer that only sets a
  brightness) from the base; while it is off, it lets the layers below
  through. With several on, the highest priority wins (among equal ones the
  one switched on last), and the next takes over when it goes off. A layer
  keeps its brightness and colour while off, like any light.
- **Hidden:** all layers, the base too, are hidden. Area, device and label
  targets, area cards and Assist reach the real light only, so "all lights in
  the garden on" never leaves a layer on, and nothing is counted twice.
  Templates that count lights themselves leave the layers out with
  `| reject('in', integration_entities('light_layers'))`.
- **Actions for the base:** `light_layers.turn_on`, `turn_off` and `toggle`
  work like the light actions, with a real light that has layers standing for
  its base. So a label, area or device target, which names the real lights,
  switches their base layers, and other lights as `light.turn_on` would:

  ```yaml
  action: light_layers.turn_off
  target:
    label_id: outdoor_lights
  ```

  A layer named by its entity id stays itself. Use these for schedules that
  set the normal state of a group of lights.
- **Timing is the automation's:** a layer stays on until something turns it
  off, so "2 minutes after the door closed" is an ordinary trigger with
  `for:` (see below), and opening the door again simply starts over.
- **At the real light:** any change that did not come through a layer is
  taken as meant for the real light itself (its wall switch, the vendor app,
  an automation for a button at the light): it becomes the base, and the
  layers above go off. So everything that should respect the layers
  (schedules, scenes) must target the base or a layer.
  A change is on/off, brightness, colour (temperature) or effect beyond a small
  tolerance; how the light renders a command (clamped, rounded, gamut-clipped)
  and reports that change nothing shown are not changes.
- **Effects:** when a layer with an effect goes off, or the layer shown has
  none, the light's own "no effect" (`off`, `none`, `Solid`, …) is sent.
- **Wall switch:** for a smart bulb behind an ordinary switch, turn on
  *Powered through a wall switch* (*Options → Settings*): coming back on after
  being unreachable is then someone at the switch and becomes the base. Without
  it, the light is brought back to what it should show (a power cut).
- **Renaming the real light:** the layers follow it (and keep their own ids).
- **Restarts:** all layers are restored; on start and when the real light comes
  back from unavailable, it is brought in line. With nothing to restore, the
  base takes the real light as it is.
- **Brightness the base doesn't name:** a bare `turn_on`, or a base recorded
  while the light was off, has no brightness. The base takes it (and the
  colour) from what the real light shows, so after a layer goes off the light
  doesn't stay at the layer's brightness.

The base's attribute `showing` names the layer on the real light right now
(`base` for itself); each layer has `priority` and `showing` (true or false).
All layers sit on the real light's device.

## Example: brighter while the door is open, and 2 minutes after

A layer *Door* (priority 10) on `light.hausnummer`:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.front_door
    to: "on"
    id: open
  - trigger: state
    entity_id: binary_sensor.front_door
    to: "off"
    for: { minutes: 2 }
    id: closed
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: open
          - condition: state
            entity_id: sun.sun
            state: below_horizon
        sequence:
          - action: light.turn_on
            target: { entity_id: light.hausnummer_door }
            data: { brightness_pct: 100 }
      - conditions:
          - condition: trigger
            id: closed
        sequence:
          - action: light.turn_off
            target: { entity_id: light.hausnummer_door }
mode: queued
```

## Setting it up for an existing light

1. Create the helper for the light.
2. Point the schedules and scenes that set the light's normal state at
   `<light>_base`, or switch them to the `light_layers` actions (which keep
   their area, device and label targets). Anything that still goes to the real light directly counts as
   a change at the light: it becomes the base and turns the layers off.
3. Point automations for temporary changes at a layer.

## Development

```
python -m venv .venv && .venv/bin/pip install -r requirements.test.txt
.venv/bin/pytest
```
