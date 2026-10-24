DOMAIN = "light_layers"

CONF_LIGHT = "light"
# the light is powered through a wall switch: coming back on is someone at the switch
CONF_WALL_SWITCH = "wall_switch"
# the layers above the base: [{"id", "name", "priority"}]
CONF_LAYERS = "layers"
CONF_PRIORITY = "priority"
# the base took over the real light's entity id; the real light became <id>_raw
CONF_TAKE_OVER = "take_over"
CONF_ENTITY_ID = "entity_id"  # the base's entity id (the real light's old one)
CONF_LAYER = "layer"  # a first layer, set up with the light
FIRST_PRIORITY = 10
SUBENTRY_LIGHT = "light"  # subentry type: one layered light
