from __future__ import annotations

from copy import deepcopy
from typing import Any

from rlm_poc.schema.iot_light import ROOMS


class MockLightExecutor:
    def __init__(self) -> None:
        self.devices = {
            room: {
                "room": room,
                "state": "off",
                "brightness": 0,
                "color_temp": "neutral",
            }
            for room in ROOMS
        }
        self.schedules: list[dict[str, Any]] = []
        self.scenes: dict[str, list[dict[str, Any]]] = {}

    def execute(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        name = tool_call["name"]
        args = tool_call.get("arguments", {})
        method = getattr(self, f"_execute_{name}", None)
        if method is None:
            raise ValueError(f"unsupported tool: {name}")
        return method(args)

    def _execute_list_devices(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"devices": deepcopy(list(self.devices.values()))}

    def _execute_get_light_state(self, args: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(self.devices[args["room"]])

    def _execute_set_light(self, args: dict[str, Any]) -> dict[str, Any]:
        device = self.devices[args["room"]]
        device["state"] = args["state"]
        if "brightness" in args:
            device["brightness"] = args["brightness"]
        elif args["state"] == "off":
            device["brightness"] = 0
        elif device["brightness"] == 0:
            device["brightness"] = 100
        if "color_temp" in args:
            device["color_temp"] = args["color_temp"]
        return deepcopy(device)

    def _execute_schedule_light(self, args: dict[str, Any]) -> dict[str, Any]:
        schedule = deepcopy(args)
        self.schedules.append(schedule)
        return {"scheduled": schedule}

    def _execute_create_scene(self, args: dict[str, Any]) -> dict[str, Any]:
        self.scenes[args["name"]] = deepcopy(args["actions"])
        return {"scene": args["name"], "actions": deepcopy(args["actions"])}
