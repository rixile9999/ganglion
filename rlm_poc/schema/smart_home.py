from __future__ import annotations

from rlm_poc.dsl.catalog import Catalog
from rlm_poc.dsl.tool_spec import EnumArg, IntArg, StringArg, TimeArg, ToolSpec
from rlm_poc.schema.home_iot import HOME_IOT_TOOLS
from rlm_poc.schema.iot_light import (
    IOT_LIGHT_EXAMPLES,
    IOT_LIGHT_RULES,
    ROOM_ARG,
)

WATER_HEATER_MODES = ("heat", "off")
DISHWASHER_CYCLES = ("normal", "eco", "quick")
WASHER_CYCLES = ("normal", "delicate", "quick")
WASHER_TEMPS = ("cold", "warm", "hot")
DRYER_MODES = ("normal", "delicate", "quick")
OVEN_MODES = ("bake", "broil", "roast")
FRIDGE_COMPARTMENTS = ("fridge", "freezer")
MICROWAVE_POWER = ("low", "medium", "high")
SPRINKLER_ZONES = ("front", "back", "garden")
AIR_PURIFIER_MODES = ("low", "medium", "high", "auto", "off")
CAMERA_LOCATIONS = ("front_door", "backyard", "garage", "living")
TV_ROOMS = ("living", "bedroom", "kitchen", "office")

WATER_HEATER_MODE_ARG = EnumArg(values=WATER_HEATER_MODES)
WATER_HEATER_TEMP_ARG = IntArg(min_value=30, max_value=70, required=False)
DISHWASHER_CYCLE_ARG = EnumArg(values=DISHWASHER_CYCLES)
WASHER_CYCLE_ARG = EnumArg(values=WASHER_CYCLES)
WASHER_TEMP_ARG = EnumArg(values=WASHER_TEMPS, required=False)
DRYER_MODE_ARG = EnumArg(values=DRYER_MODES)
OVEN_MODE_ARG = EnumArg(values=OVEN_MODES)
OVEN_TEMP_ARG = IntArg(min_value=50, max_value=300)
FRIDGE_COMPARTMENT_ARG = EnumArg(values=FRIDGE_COMPARTMENTS)
FRIDGE_TEMP_ARG = IntArg(min_value=-25, max_value=10)
MICROWAVE_DURATION_ARG = IntArg(min_value=1, max_value=3600)
MICROWAVE_POWER_ARG = EnumArg(values=MICROWAVE_POWER, required=False)
SPRINKLER_ZONE_ARG = EnumArg(values=SPRINKLER_ZONES)
SPRINKLER_DURATION_ARG = IntArg(min_value=1, max_value=120)
POOL_TEMP_ARG = IntArg(min_value=15, max_value=40)
POSITION_ARG = IntArg(min_value=0, max_value=100)
FAN_SPEED_ARG = IntArg(min_value=0, max_value=100)
FAN_STATE_ARG = EnumArg(values=("on", "off"))
AIR_PURIFIER_MODE_ARG = EnumArg(values=AIR_PURIFIER_MODES)
HUMIDITY_LEVEL_ARG = IntArg(min_value=0, max_value=100)
CAMERA_LOCATION_ARG = EnumArg(values=CAMERA_LOCATIONS)
TIMER_DURATION_ARG = IntArg(min_value=1, max_value=86400)
TV_ROOM_ARG = EnumArg(values=TV_ROOMS)
QUANTITY_ARG = IntArg(min_value=1, max_value=100, required=False)


SMART_HOME_EXTRA_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="set_water_heater",
        description="Configure the water heater mode and target temperature.",
        args=(("mode", WATER_HEATER_MODE_ARG), ("temperature", WATER_HEATER_TEMP_ARG)),
    ),
    ToolSpec(
        name="start_dishwasher",
        description="Start the dishwasher with a cycle.",
        args=(("cycle", DISHWASHER_CYCLE_ARG),),
    ),
    ToolSpec(
        name="stop_dishwasher",
        description="Stop the dishwasher.",
    ),
    ToolSpec(
        name="start_washer",
        description="Start the clothes washer.",
        args=(("cycle", WASHER_CYCLE_ARG), ("temperature", WASHER_TEMP_ARG)),
    ),
    ToolSpec(
        name="stop_washer",
        description="Stop the clothes washer.",
    ),
    ToolSpec(
        name="start_dryer",
        description="Start the clothes dryer.",
        args=(("mode", DRYER_MODE_ARG),),
    ),
    ToolSpec(
        name="stop_dryer",
        description="Stop the clothes dryer.",
    ),
    ToolSpec(
        name="start_oven",
        description="Start the oven with a cooking mode.",
        args=(("mode", OVEN_MODE_ARG), ("temperature", OVEN_TEMP_ARG)),
    ),
    ToolSpec(
        name="stop_oven",
        description="Stop the oven.",
    ),
    ToolSpec(
        name="set_fridge_temp",
        description="Set the fridge or freezer temperature.",
        args=(("compartment", FRIDGE_COMPARTMENT_ARG), ("temperature", FRIDGE_TEMP_ARG)),
    ),
    ToolSpec(
        name="start_microwave",
        description="Run the microwave for a duration.",
        args=(("duration_seconds", MICROWAVE_DURATION_ARG), ("power", MICROWAVE_POWER_ARG)),
    ),
    ToolSpec(
        name="order_groceries",
        description="Order an item from the grocery service.",
        args=(("item", StringArg()), ("quantity", QUANTITY_ARG)),
    ),
    ToolSpec(
        name="start_sprinkler",
        description="Run a sprinkler zone for a duration.",
        args=(("zone", SPRINKLER_ZONE_ARG), ("duration_minutes", SPRINKLER_DURATION_ARG)),
    ),
    ToolSpec(
        name="stop_sprinkler",
        description="Stop a sprinkler zone.",
        args=(("zone", SPRINKLER_ZONE_ARG),),
    ),
    ToolSpec(
        name="start_pool_pump",
        description="Start the pool circulation pump.",
    ),
    ToolSpec(
        name="stop_pool_pump",
        description="Stop the pool circulation pump.",
    ),
    ToolSpec(
        name="set_pool_temp",
        description="Set the pool target temperature.",
        args=(("temperature", POOL_TEMP_ARG),),
    ),
    ToolSpec(
        name="open_window",
        description="Open a window in a room.",
        args=(("room", ROOM_ARG), ("position", POSITION_ARG)),
    ),
    ToolSpec(
        name="close_window",
        description="Close a window in a room.",
        args=(("room", ROOM_ARG),),
    ),
    ToolSpec(
        name="set_fan",
        description="Configure a room fan.",
        args=(("room", ROOM_ARG), ("speed", FAN_SPEED_ARG), ("state", FAN_STATE_ARG)),
    ),
    ToolSpec(
        name="set_air_purifier",
        description="Configure the air purifier mode.",
        args=(("room", ROOM_ARG), ("mode", AIR_PURIFIER_MODE_ARG)),
    ),
    ToolSpec(
        name="start_humidifier",
        description="Start the humidifier at a level.",
        args=(("room", ROOM_ARG), ("level", HUMIDITY_LEVEL_ARG)),
    ),
    ToolSpec(
        name="start_camera_recording",
        description="Start recording on a camera.",
        args=(("location", CAMERA_LOCATION_ARG),),
    ),
    ToolSpec(
        name="stop_camera_recording",
        description="Stop recording on a camera.",
        args=(("location", CAMERA_LOCATION_ARG),),
    ),
    ToolSpec(
        name="send_email",
        description="Send an email.",
        args=(
            ("to", StringArg()),
            ("subject", StringArg()),
            ("body", StringArg()),
        ),
    ),
    ToolSpec(
        name="send_sms",
        description="Send an SMS message.",
        args=(("to", StringArg()), ("message", StringArg())),
    ),
    ToolSpec(
        name="set_reminder",
        description="Create a reminder at a time.",
        args=(("text", StringArg()), ("at", TimeArg())),
    ),
    ToolSpec(
        name="start_timer",
        description="Start a countdown timer.",
        args=(("label", StringArg()), ("duration_seconds", TIMER_DURATION_ARG)),
    ),
    ToolSpec(
        name="get_weather_forecast",
        description="Get the weather forecast.",
        args=(("city", StringArg(required=False)),),
    ),
    ToolSpec(
        name="play_tv",
        description="Start playing TV in a room.",
        args=(("room", TV_ROOM_ARG), ("channel", StringArg(required=False))),
    ),
)


SMART_HOME_TOOLS: tuple[ToolSpec, ...] = HOME_IOT_TOOLS + SMART_HOME_EXTRA_TOOLS


CATALOG = Catalog(
    name="smart_home_50",
    tools=SMART_HOME_TOOLS,
    examples=IOT_LIGHT_EXAMPLES,
    extra_rules=IOT_LIGHT_RULES,
)
