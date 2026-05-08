# Eval report — iot_light_5

- train: 100
- holdout: 26

## Headline metrics

- syntax_valid_rate: **100.0%**
- exact_match_rate:  **88.5%**
- action_match_rate: **100.0%**
- latency P50: 1877 ms
- latency P95: 2873 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| tool_anchored:schedule_light | 6 | 100.0% | 100.0% | 83.3% |
| tool_anchored:create_scene | 5 | 100.0% | 100.0% | 80.0% |
| tool_anchored:get_light_state | 4 | 100.0% | 100.0% | 100.0% |
| tool_anchored:set_light | 6 | 100.0% | 100.0% | 83.3% |
| tool_anchored:list_devices | 5 | 100.0% | 100.0% | 100.0% |

## Failures (3)

### `12427d1b`
**prompt:** 침실 조명을 밤 11시에 10% 밝기로 꺼줘.
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "23:00", "state": "off", "brightness": 10}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "22:30", "state": "off", "brightness": 10}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "22:30", "brightness": 10, "room": "bedroom", "state": "off"}}]}`

### `ec866369`
**prompt:** Set up a movie scene with the living room lights on and dimmed to 30%.
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 30}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 30, "color_temp": "warm"}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 30, "color_temp": "warm", "room": "living", "state": "on"}}], "name": "movie"}}]}`

### `e822befd`
**prompt:** 침실 조명을 차갑게 켜고 밝기는 80으로 설정해 줘.
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 80, "color_temp": "cool"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 80, "color_temp": "warm"}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"brightness": 80, "color_temp": "warm", "room": "bedroom", "state": "on"}}]}`
