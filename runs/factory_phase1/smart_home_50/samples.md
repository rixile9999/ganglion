# Smoke samples — smart_home_50

Sampled 20 of 441 kept examples (seed=42).

## #1  (tool_anchored:close_window)
**intent:** 주방 창문 좀 닫을래?
**dsl:** `{"calls": [{"action": "close_window", "args": {"room": "kitchen"}}]}`

## #2  (tool_anchored:set_thermostat)
**intent:** Set the bedroom thermostat to auto mode at 21C.
**dsl:** `{"calls": [{"action": "set_thermostat", "args": {"mode": "auto", "room": "bedroom", "temperature": 21}}]}`

## #3  (tool_anchored:create_scene)
**intent:** 수면 모드 설정: 주방 조명 끄기.
**dsl:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "off"}}], "name": "sleep"}}]}`

## #4  (tool_anchored:start_sprinkler)
**intent:** Run the garden zone.
**dsl:** `{"calls": [{"action": "start_sprinkler", "args": {"duration_minutes": 20, "zone": "garden"}}]}`

## #5  (tool_anchored:open_window)
**intent:** Could you please crack the bedroom window open just a tiny bit?
**dsl:** `{"calls": [{"action": "open_window", "args": {"position": 10, "room": "bedroom"}}]}`

## #6  (tool_anchored:stop_camera_recording)
**intent:** Stop recording at the front door.
**dsl:** `{"calls": [{"action": "stop_camera_recording", "args": {"location": "front_door"}}]}`

## #7  (tool_anchored:start_oven)
**intent:** Start baking at 175.
**dsl:** `{"calls": [{"action": "start_oven", "args": {"mode": "bake", "temperature": 175}}]}`

## #8  (tool_anchored:start_timer)
**intent:** Can you begin a 24-hour timer named 'Server Maintenance'?
**dsl:** `{"calls": [{"action": "start_timer", "args": {"duration_seconds": 86400, "label": "Server Maintenance"}}]}`

## #9  (tool_anchored:open_window)
**intent:** 거실 창문 좀 열어줘
**dsl:** `{"calls": [{"action": "open_window", "args": {"position": 100, "room": "living"}}]}`

## #10  (tool_anchored:stop_washer)
**intent:** Halt the current wash cycle immediately.
**dsl:** `{"calls": [{"action": "stop_washer", "args": {}}]}`

## #11  (tool_anchored:order_groceries)
**intent:** I'd like to get 24 cans of sparkling water delivered.
**dsl:** `{"calls": [{"action": "order_groceries", "args": {"item": "sparkling water", "quantity": 24}}]}`

## #12  (tool_anchored:set_fridge_temp)
**intent:** Freezer temp: -25
**dsl:** `{"calls": [{"action": "set_fridge_temp", "args": {"compartment": "freezer", "temperature": -25}}]}`

## #13  (tool_anchored:close_window)
**intent:** Close the kitchen window.
**dsl:** `{"calls": [{"action": "close_window", "args": {"room": "kitchen"}}]}`

## #14  (tool_anchored:set_reminder)
**intent:** Remind me to take the chicken out of the oven at 18:30.
**dsl:** `{"calls": [{"action": "set_reminder", "args": {"at": "18:30", "text": "take the chicken out of the oven"}}]}`

## #15  (tool_anchored:stop_sprinkler)
**intent:** The garden sprinkler needs to be shut down.
**dsl:** `{"calls": [{"action": "stop_sprinkler", "args": {"zone": "garden"}}]}`

## #16  (tool_anchored:stop_sprinkler)
**intent:** Can you kill the sprinklers for the front lawn?
**dsl:** `{"calls": [{"action": "stop_sprinkler", "args": {"zone": "front"}}]}`

## #17  (tool_anchored:open_garage)
**intent:** Open the garage door.
**dsl:** `{"calls": [{"action": "open_garage", "args": {"state": "open"}}]}`

## #18  (tool_anchored:set_light)
**intent:** 침실 조명 밝기를 30으로 맞추고 꺼주세요.
**dsl:** `{"calls": [{"action": "set_light", "args": {"brightness": 30, "room": "bedroom", "state": "off"}}]}`

## #19  (tool_anchored:schedule_light)
**intent:** 서재 조명을 오전 9시에 꺼주세요.
**dsl:** `{"calls": [{"action": "schedule_light", "args": {"at": "09:00", "room": "office", "state": "off"}}]}`

## #20  (tool_anchored:play_music)
**intent:** 침실에 클래식 음악을 볼륨 80으로 재생해 주세요.
**dsl:** `{"calls": [{"action": "play_music", "args": {"genre": "classical", "room": "bedroom", "volume": 80}}]}`
