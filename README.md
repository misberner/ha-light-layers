# Light Layers

A stand-in light for Home Assistant whose ordinary commands set the light's
*normal state*, with timed overrides on top.

The usual "brighten for 5 minutes, then restore" takes a snapshot and puts it
back. If a schedule changes the light in between, the snapshot is stale: the
light goes off immediately, or comes back at the old brightness for the rest
of the night. Here, commands during an override are recorded but not shown;
when the override ends the light shows its normal state *as it is then*.

## How it works

- **Stand-in:** add the helper (Settings → Devices & services → Helpers →
  Light Layers) for a real light. The stand-in is an ordinary light: schedules,
  scenes, dashboards and voice use it like the real one. Without an override
  it passes every command straight through. It mirrors the real light's colour
  modes, colour temperature range, effects and transitions, and sits on the
  real light's device, so area and device targets reach it.
- **Override:** `light_layers.override` shows something else for a while:
  brightness, colour temperature, colour, effect, or `off: true`. `key` names
  it: setting the same key again extends it (never shortens). With several
  active, the highest `priority` wins, and the next takes over when it ends.
  `light_layers.cancel` ends one (or all).
- **At the real light:** any change that did not come through the stand-in is
  taken as meant for the real light itself (its wall switch, the vendor app,
  an automation for a button at the light): it ends all overrides and becomes
  the new normal state. So everything that should respect overrides
  (schedules, scenes, labels used as targets) must target the stand-in.
  A change is on/off, brightness, colour (temperature) or effect beyond a small
  tolerance; how the light renders a command (clamped, rounded, gamut-clipped)
  and reports that change nothing shown are not changes.
- **Effects:** when an override with an effect ends, or the normal state has
  none, the light's own "no effect" (`off`, `none`, `Solid`, …) is sent.
- **Wall switch:** for a smart bulb behind an ordinary switch, turn on
  *Powered through a wall switch*: coming back on after being unreachable is
  then someone at the switch and becomes the normal state. Without it, the
  light is brought back to its normal state (a power cut).
- **Renaming the real light:** the stand-in follows the new entity id.
- **Restarts:** the normal state and running overrides are restored; on start
  and when the real light comes back from unavailable, it is brought in line.
  With nothing to restore, the stand-in takes the real light as it is.
- **Brightness the base doesn't name:** a bare `turn_on`, or a base recorded
  while the light was off, has no brightness. The stand-in takes it (and the
  colour) from what the real light shows, so after an override the light
  doesn't stay at the override's brightness.

The stand-in's own state is the normal state; attribute `showing` says
whether an override is on the real light right now, `overrides` lists them
with their end.

```yaml
action: light_layers.override
target:
  entity_id: light.hausnummer
data:
  key: door_night
  brightness_pct: 60
  minutes: 5
```

## Setting it up for an existing light

1. Rename the real light's entity (e.g. `light.hausnummer` →
   `light.hausnummer_raw`) and hide it.
2. Add the helper for it with the old name, so the stand-in takes over the old
   entity id and automations, scenes and dashboards use it unchanged.
3. Move labels used as targets to the stand-in: Home Assistant skips hidden
   entities for label, area and device targets, so the hidden real light no
   longer gets them. Area and device targets reach the stand-in on their own.
4. Expose the stand-in, not the real light, to voice assistants and HomeKit.

## Development

```
python -m venv .venv && .venv/bin/pip install -r requirements.test.txt
.venv/bin/pytest
```
