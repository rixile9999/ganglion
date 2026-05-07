# Eval report — smart_home_50

- train: 349
- holdout: 92

## Headline metrics

- syntax_valid_rate: **97.8%**
- exact_match_rate:  **85.9%**
- action_match_rate: **93.5%**
- latency P50: 1072 ms
- latency P95: 1664 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| tool_anchored:set_fridge_temp | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_camera_recording | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:stop_camera_recording | 2 | 100.0% | 100.0% | 50.0% |
| tool_anchored:order_groceries | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:arm_security | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:set_light | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:set_water_heater | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_timer | 2 | 50.0% | 50.0% | 50.0% |
| tool_anchored:set_reminder | 2 | 100.0% | 50.0% | 50.0% |
| tool_anchored:get_weather_forecast | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_robot_vacuum | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:set_music_volume | 1 | 100.0% | 100.0% | 100.0% |
| tool_anchored:set_thermostat | 2 | 100.0% | 50.0% | 50.0% |
| tool_anchored:set_alarm | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:stop_dryer | 1 | 100.0% | 100.0% | 100.0% |
| tool_anchored:send_sms | 2 | 100.0% | 100.0% | 50.0% |
| tool_anchored:stop_music | 1 | 100.0% | 100.0% | 100.0% |
| tool_anchored:close_window | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_washer | 2 | 100.0% | 50.0% | 0.0% |
| tool_anchored:send_notification | 2 | 100.0% | 50.0% | 0.0% |
| tool_anchored:set_air_purifier | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:stop_robot_vacuum | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:schedule_light | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:set_fan | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_dryer | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:open_garage | 2 | 100.0% | 100.0% | 50.0% |
| tool_anchored:play_tv | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_pool_pump | 1 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_dishwasher | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:get_thermostat | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:list_devices | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:stop_washer | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:set_pool_temp | 1 | 100.0% | 100.0% | 100.0% |
| tool_anchored:send_email | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_humidifier | 2 | 100.0% | 100.0% | 50.0% |
| tool_anchored:get_light_state | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:open_window | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:lock_door | 1 | 100.0% | 100.0% | 100.0% |
| tool_anchored:create_scene | 1 | 100.0% | 100.0% | 100.0% |
| tool_anchored:stop_dishwasher | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:stop_oven | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:stop_pool_pump | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:set_curtain | 2 | 100.0% | 100.0% | 50.0% |
| tool_anchored:cancel_alarm | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_oven | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:play_music | 1 | 100.0% | 100.0% | 100.0% |
| tool_anchored:stop_sprinkler | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:unlock_door | 2 | 100.0% | 100.0% | 100.0% |
| tool_anchored:start_sprinkler | 2 | 50.0% | 50.0% | 50.0% |
| tool_anchored:start_microwave | 2 | 100.0% | 100.0% | 100.0% |

## Failures (13)

### `02107dc1`
**prompt:** I need a 10 minute reminder to check the oven, please label it 'Check Oven'
**expected:** `{"calls": [{"action": "start_timer", "args": {"label": "Check Oven", "duration_seconds": 600}}]}`
**predicted:** *(parse failed)*
**error:** set_reminder: unknown arg 'label'
**raw:** `{"calls": [{"action": "set_reminder", "args": {"at": "00:10", "text": "Check Oven", "label": "Check Oven"}}]}`

### `13935f65`
**prompt:** Please set an alarm for 09:00 to take the trash out.
**expected:** `{"calls": [{"action": "set_reminder", "args": {"text": "take the trash out", "at": "09:00"}}]}`
**predicted:** `{"calls": [{"action": "set_alarm", "args": {"at": "09:00"}}]}`
**raw:** `{"calls": [{"action": "set_alarm", "args": {"at": "09:00"}}]}`

### `e4e9000f`
**prompt:** I need to wash these silks gently.
**expected:** `{"calls": [{"action": "start_washer", "args": {"cycle": "delicate"}}]}`
**predicted:** `{"calls": [{"action": "start_washer", "args": {"cycle": "delicate", "temperature": "warm"}}]}`
**raw:** `{"calls": [{"action": "start_washer", "args": {"cycle": "delicate", "temperature": "warm"}}]}`

### `6698b2dd`
**prompt:** Text +1-555-0123 that I'll be five minutes late.
**expected:** `{"calls": [{"action": "send_sms", "args": {"to": "+1-555-0123", "message": "I'll be five minutes late."}}]}`
**predicted:** `{"calls": [{"action": "send_sms", "args": {"to": "1-555-0123", "message": "I'll be five minutes late."}}]}`
**raw:** `{"calls": [{"action": "send_sms", "args": {"message": "I'll be five minutes late.", "to": "1-555-0123"}}]}`

### `3401465b`
**prompt:** Note to self: buy milk.
**expected:** `{"calls": [{"action": "send_notification", "args": {"message": "buy milk", "recipient": "self"}}]}`
**predicted:** `{"calls": [{"action": "order_groceries", "args": {"item": "milk"}}]}`
**raw:** `{"calls": [{"action": "order_groceries", "args": {"item": "milk"}}]}`

### `a31d849d`
**prompt:** Just start the machine on the quick setting.
**expected:** `{"calls": [{"action": "start_washer", "args": {"cycle": "quick"}}]}`
**predicted:** `{"calls": [{"action": "start_dishwasher", "args": {"cycle": "quick"}}]}`
**raw:** `{"calls": [{"action": "start_dishwasher", "args": {"cycle": "quick"}}]}`

### `befdcb69`
**prompt:** Can you lower the garage door for me?
**expected:** `{"calls": [{"action": "open_garage", "args": {"state": "close"}}]}`
**predicted:** `{"calls": [{"action": "open_garage", "args": {"state": "open"}}]}`
**raw:** `{"calls": [{"action": "open_garage", "args": {"state": "open"}}]}`

### `ae580538`
**prompt:** 주방에서 가습기를 weakest 모드로 돌려줘.
**expected:** `{"calls": [{"action": "start_humidifier", "args": {"room": "kitchen", "level": 10}}]}`
**predicted:** `{"calls": [{"action": "start_humidifier", "args": {"room": "kitchen", "level": 0}}]}`
**raw:** `{"calls": [{"action": "start_humidifier", "args": {"level": 0, "room": "kitchen"}}]}`

### `e2347e6c`
**prompt:** Turn off garage cam recording.
**expected:** `{"calls": [{"action": "stop_camera_recording", "args": {"location": "garage"}}]}`
**predicted:** `{"calls": [{"action": "stop_camera_recording", "args": {"location": "front_door"}}]}`
**raw:** `{"calls": [{"action": "stop_camera_recording", "args": {"location": "front_door"}}]}`

### `3aea66ae`
**prompt:** Open the living room curtains completely.
**expected:** `{"calls": [{"action": "set_curtain", "args": {"room": "living", "state": "open", "position": 100}}]}`
**predicted:** `{"calls": [{"action": "set_curtain", "args": {"room": "living", "state": "open"}}]}`
**raw:** `{"calls": [{"action": "set_curtain", "args": {"room": "living", "state": "open"}}]}`

### `580713ab`
**prompt:** Turn off the kitchen heating.
**expected:** `{"calls": [{"action": "set_thermostat", "args": {"room": "kitchen", "temperature": 10, "mode": "off"}}]}`
**predicted:** `{"calls": [{"action": "set_water_heater", "args": {"mode": "off"}}]}`
**raw:** `{"calls": [{"action": "set_water_heater", "args": {"mode": "off"}}]}`

### `f5dd7f7a`
**prompt:** Run the garden zone.
**expected:** `{"calls": [{"action": "start_sprinkler", "args": {"zone": "garden", "duration_minutes": 20}}]}`
**predicted:** *(parse failed)*
**error:** start_sprinkler.duration_minutes is required
**raw:** `{"calls": [{"action": "start_sprinkler", "args": {"zone": "garden"}}]}`

### `68763146`
**prompt:** Alert the household that the front door is unlocked.
**expected:** `{"calls": [{"action": "send_notification", "args": {"message": "the front door is unlocked", "recipient": "family"}}]}`
**predicted:** `{"calls": [{"action": "send_notification", "args": {"message": "front door is unlocked", "recipient": "family"}}]}`
**raw:** `{"calls": [{"action": "send_notification", "args": {"message": "front door is unlocked", "recipient": "family"}}]}`
