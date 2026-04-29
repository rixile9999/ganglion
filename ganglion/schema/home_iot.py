from __future__ import annotations

from ganglion.dsl.catalog import Catalog
from ganglion.dsl.tool_spec import EnumArg, IntArg, StringArg, TimeArg, ToolSpec
from ganglion.schema.iot_light import (
    IOT_LIGHT_EXAMPLES,
    IOT_LIGHT_RULES,
    IOT_LIGHT_TOOLS,
    ROOM_ARG,
)

CURTAIN_STATES = ("open", "close", "stop")
THERMOSTAT_MODES = ("heat", "cool", "auto", "off")
DOORS = ("front", "back", "garage")
SECURITY_MODES = ("home", "away", "off")
GARAGE_STATES = ("open", "close")
RECIPIENTS = ("family", "self")
VACUUM_AREAS = ("living", "bedroom", "kitchen", "hallway", "office", "all")

CURTAIN_STATE_ARG = EnumArg(values=CURTAIN_STATES)
CURTAIN_POSITION_ARG = IntArg(min_value=0, max_value=100, required=False)
THERMOSTAT_MODE_ARG = EnumArg(values=THERMOSTAT_MODES)
TEMPERATURE_C_ARG = IntArg(min_value=10, max_value=35)
DOOR_ARG = EnumArg(values=DOORS)
SECURITY_MODE_ARG = EnumArg(values=SECURITY_MODES)
GARAGE_STATE_ARG = EnumArg(values=GARAGE_STATES)
RECIPIENT_ARG = EnumArg(values=RECIPIENTS, required=False)
VOLUME_ARG = IntArg(min_value=0, max_value=100)
VOLUME_OPTIONAL = IntArg(min_value=0, max_value=100, required=False)
GENRE_ARG = StringArg(required=False)
ALARM_LABEL_ARG = StringArg(required=False)
NOTIFICATION_BODY_ARG = StringArg()
VACUUM_AREA_ARG = EnumArg(values=VACUUM_AREAS, required=False)


HOME_IOT_EXTRA_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="set_curtain",
        description="Open, close, or stop a curtain in a room.",
        args=(
            ("room", ROOM_ARG),
            ("state", CURTAIN_STATE_ARG),
            ("position", CURTAIN_POSITION_ARG),
        ),
    ),
    ToolSpec(
        name="set_thermostat",
        description="Set the thermostat for a room.",
        args=(
            ("room", ROOM_ARG),
            ("temperature", TEMPERATURE_C_ARG),
            ("mode", THERMOSTAT_MODE_ARG),
        ),
    ),
    ToolSpec(
        name="get_thermostat",
        description="Get the current thermostat reading for a room.",
        args=(("room", ROOM_ARG),),
    ),
    ToolSpec(
        name="play_music",
        description="Play music in a room.",
        args=(
            ("room", ROOM_ARG),
            ("genre", GENRE_ARG),
            ("volume", VOLUME_OPTIONAL),
        ),
    ),
    ToolSpec(
        name="stop_music",
        description="Stop music in a room.",
        args=(("room", ROOM_ARG),),
    ),
    ToolSpec(
        name="set_music_volume",
        description="Set the music volume for a room.",
        args=(("room", ROOM_ARG), ("volume", VOLUME_ARG)),
    ),
    ToolSpec(
        name="lock_door",
        description="Lock a door.",
        args=(("door", DOOR_ARG),),
    ),
    ToolSpec(
        name="unlock_door",
        description="Unlock a door.",
        args=(("door", DOOR_ARG),),
    ),
    ToolSpec(
        name="arm_security",
        description="Set the home security alarm mode.",
        args=(("mode", SECURITY_MODE_ARG),),
    ),
    ToolSpec(
        name="set_alarm",
        description="Set a wake or reminder alarm.",
        args=(("at", TimeArg()), ("label", ALARM_LABEL_ARG)),
    ),
    ToolSpec(
        name="cancel_alarm",
        description="Cancel a previously set alarm by label.",
        args=(("label", StringArg()),),
    ),
    ToolSpec(
        name="start_robot_vacuum",
        description="Start the robot vacuum.",
        args=(("area", VACUUM_AREA_ARG),),
    ),
    ToolSpec(
        name="stop_robot_vacuum",
        description="Stop the robot vacuum.",
    ),
    ToolSpec(
        name="open_garage",
        description="Open or close the garage door.",
        args=(("state", GARAGE_STATE_ARG),),
    ),
    ToolSpec(
        name="send_notification",
        description="Send a notification to a recipient.",
        args=(
            ("message", NOTIFICATION_BODY_ARG),
            ("recipient", RECIPIENT_ARG),
        ),
    ),
)


HOME_IOT_TOOLS: tuple[ToolSpec, ...] = IOT_LIGHT_TOOLS + HOME_IOT_EXTRA_TOOLS


CATALOG = Catalog(
    name="home_iot_20",
    tools=HOME_IOT_TOOLS,
    examples=IOT_LIGHT_EXAMPLES,
    extra_rules=IOT_LIGHT_RULES,
)
