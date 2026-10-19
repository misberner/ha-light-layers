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
  modes, colour temperature range, effects and transitions.
- **Override:** `light_layers.override` shows something else for a while:
  brightness, colour temperature, colour, effect, or `off: true`. `key` names
  it: setting the same key again extends it (never shortens). With several
  active, the highest `priority` wins, and the next takes over when it ends.
  `light_layers.cancel` ends one (or all).
- **By hand:** a change at the real light that did not come from the stand-in
  (wall switch, the vendor app, something targeting the real light) ends all
  overrides and becomes the new normal state.
- **Restarts:** the normal state and running overrides are restored; on start
  and when the real light comes back from unavailable, it is brought in line.

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
3. Point device targets at the entity instead (a device target still reaches
   the real light), and move labels used as targets to the stand-in.

## Development

```
python -m venv .venv && .venv/bin/pip install -r requirements.test.txt
.venv/bin/pytest
```
