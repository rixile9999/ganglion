# Eval report — iot_light_5

- train: 100
- holdout: 26

## Headline metrics

- syntax_valid_rate: **100.0%**
- exact_match_rate:  **92.3%**
- action_match_rate: **100.0%**
- latency P50: 1615 ms
- latency P95: 2338 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| tool_anchored:schedule_light | 6 | 100.0% | 100.0% | 100.0% |
| tool_anchored:create_scene | 5 | 100.0% | 100.0% | 60.0% |
| tool_anchored:get_light_state | 4 | 100.0% | 100.0% | 100.0% |
| tool_anchored:set_light | 6 | 100.0% | 100.0% | 100.0% |
| tool_anchored:list_devices | 5 | 100.0% | 100.0% | 100.0% |

## Failures (2)

### `8c63292b`
**prompt:** 휴식 모드를 만들어줘. 침실 조명을 켜고 색온도를 따뜻한 느낌으로 바꿔.
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "relax", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "sleep", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 100, "color_temp": "warm"}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 100, "color_temp": "warm", "room": "bedroom", "state": "on"}}], "name": "sleep"}}]}`

### `14023768`
**prompt:** I need a focus mode for reading in the study.
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "focus", "actions": [{"action": "set_light", "args": {"room": "office", "state": "on"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "focus", "actions": [{"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "room": "office", "state": "on"}}], "name": "focus"}}]}`
