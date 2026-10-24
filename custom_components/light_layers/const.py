DOMAIN = "light_layers"

CONF_LIGHT = "light"
# the light is powered through a wall switch: coming back on is someone at the switch
CONF_WALL_SWITCH = "wall_switch"
# the layers above the base: [{"id", "name", "priority"}]
CONF_LAYERS = "layers"
CONF_PRIORITY = "priority"
CONF_LAYER = "layer"  # a first layer, set up with the light
FIRST_PRIORITY = 10
BASE_SUFFIX = "_base"  # the base layer: <real light>_base
