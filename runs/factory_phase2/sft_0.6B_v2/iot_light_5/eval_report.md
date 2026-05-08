# Eval report — iot_light_5

- train: 271
- holdout: 70

## Headline metrics

- syntax_valid_rate: **98.6%**
- exact_match_rate:  **97.1%**
- action_match_rate: **98.6%**
- latency P50: 1611 ms
- latency P95: 3022 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| bootstrap:s0 | 47 | 97.9% | 97.9% | 97.9% |
| tool_anchored:set_light | 4 | 100.0% | 100.0% | 100.0% |
| tool_anchored:schedule_light | 5 | 100.0% | 100.0% | 80.0% |
| tool_anchored:create_scene | 4 | 100.0% | 100.0% | 100.0% |
| tool_anchored:list_devices | 4 | 100.0% | 100.0% | 100.0% |
| tool_anchored:get_light_state | 4 | 100.0% | 100.0% | 100.0% |
| bootstrap:s2 | 1 | 100.0% | 100.0% | 100.0% |
| bootstrap:s1 | 1 | 100.0% | 100.0% | 100.0% |

## Failures (2)

### `8f738131`
**prompt:** 서재 조명을 오후 2시에 50% 밝기로 켜주세요.
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "office", "at": "14:00", "state": "on", "brightness": 50}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "office", "at": "22:00", "state": "on", "brightness": 50}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "22:00", "brightness": 50, "room": "office", "state": "on"}}]}`

### `97ebcc04`
**prompt:** 오피스 등 cool tone으로 brightest인 100으로 설정해 주세요.
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 100, "color_temp": "cool"}}]}`
**predicted:** *(parse failed)*
**error:** set_light.room is required
**raw:** `{"calls": [{"action": "set_light", "args": {"brightness": 100, "color_temp": "cool"}}]}`
