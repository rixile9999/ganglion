# Smoke samples — iot_light_5

Sampled 20 of 126 kept examples (seed=42).

## #1  (tool_anchored:create_scene)
**intent:** Create a relax scene that turns on the bedroom lamp.
**dsl:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on"}}], "name": "relax"}}]}`

## #2  (tool_anchored:get_light_state)
**intent:** 주방 불 꺼져 있어?
**dsl:** `{"calls": [{"action": "get_light_state", "args": {"room": "kitchen"}}]}`

## #3  (tool_anchored:schedule_light)
**intent:** Set the kitchen lights to turn on at 6:15 AM.
**dsl:** `{"calls": [{"action": "schedule_light", "args": {"at": "06:15", "room": "kitchen", "state": "on"}}]}`

## #4  (tool_anchored:create_scene)
**intent:** 영화 감상 모드를 만들어줘. 거실 조명은 켜고 침실 조명은 꺼줘.
**dsl:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "living", "state": "on"}}, {"action": "set_light", "args": {"room": "bedroom", "state": "off"}}], "name": "movie"}}]}`

## #5  (tool_anchored:set_light)
**intent:** Set the kitchen brightness to 80 and make it warm.
**dsl:** `{"calls": [{"action": "set_light", "args": {"brightness": 80, "color_temp": "warm", "room": "kitchen", "state": "on"}}]}`

## #6  (tool_anchored:schedule_light)
**intent:** Can you make sure the kitchen lights go on at 18:00?
**dsl:** `{"calls": [{"action": "schedule_light", "args": {"at": "18:00", "room": "kitchen", "state": "on"}}]}`

## #7  (tool_anchored:create_scene)
**intent:** I need a focus scene for reading. Turn on the study light at full brightness.
**dsl:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 100, "room": "office", "state": "on"}}], "name": "focus"}}]}`

## #8  (tool_anchored:create_scene)
**intent:** Create a relax scene that turns on the kitchen and hallway lights.
**dsl:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on"}}, {"action": "set_light", "args": {"room": "hallway", "state": "on"}}], "name": "relax"}}]}`

## #9  (tool_anchored:schedule_light)
**intent:** Please schedule the living room lights to turn on at 07:30 with 80% brightness.
**dsl:** `{"calls": [{"action": "schedule_light", "args": {"at": "07:30", "brightness": 80, "room": "living", "state": "on"}}]}`

## #10  (tool_anchored:schedule_light)
**intent:** 침실 조명을 밤 10시 30분에 꺼줘.
**dsl:** `{"calls": [{"action": "schedule_light", "args": {"at": "22:30", "room": "bedroom", "state": "off"}}]}`

## #11  (tool_anchored:create_scene)
**intent:** 수면 모드 설정: 거실과 복도 조명을 모두 꺼줘.
**dsl:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "living", "state": "off"}}, {"action": "set_light", "args": {"room": "hallway", "state": "off"}}], "name": "sleep"}}]}`

## #12  (tool_anchored:get_light_state)
**intent:** What's the status of the office lamp?
**dsl:** `{"calls": [{"action": "get_light_state", "args": {"room": "office"}}]}`

## #13  (tool_anchored:list_devices)
**intent:** List devices
**dsl:** `{"calls": [{"action": "list_devices", "args": {}}]}`

## #14  (tool_anchored:list_devices)
**intent:** Give me a roster of installed lights.
**dsl:** `{"calls": [{"action": "list_devices", "args": {}}]}`

## #15  (tool_anchored:get_light_state)
**intent:** What's the current state of the kitchen lights?
**dsl:** `{"calls": [{"action": "get_light_state", "args": {"room": "kitchen"}}]}`

## #16  (tool_anchored:set_light)
**intent:** Switch off the kitchen light.
**dsl:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "off"}}]}`

## #17  (tool_anchored:list_devices)
**intent:** What lights can I control?
**dsl:** `{"calls": [{"action": "list_devices", "args": {}}]}`

## #18  (tool_anchored:schedule_light)
**intent:** 거실 불을 저녁 8시에 꺼줘.
**dsl:** `{"calls": [{"action": "schedule_light", "args": {"at": "20:00", "room": "living", "state": "off"}}]}`

## #19  (tool_anchored:create_scene)
**intent:** I need a focus mode for reading in the study.
**dsl:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "office", "state": "on"}}], "name": "focus"}}]}`

## #20  (tool_anchored:get_light_state)
**intent:** Tell me the status of the office lights.
**dsl:** `{"calls": [{"action": "get_light_state", "args": {"room": "office"}}]}`
