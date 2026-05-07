# Eval report — iot_light_5

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **99.4%**
- exact_match_rate:  **93.8%**
- action_match_rate: **95.6%**
- latency P50: 1427 ms
- latency P95: 2539 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 99.4% | 95.6% | 93.8% |

## Failures (31)

### `a05f993e`
**prompt:** 오후 1시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "01:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "01:00", "room": "living", "state": "off"}}]}`

### `609dd410`
**prompt:** create movie mode scene. set bedroom light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "room": "bedroom", "color_temp": "warm"}}], "name": "movie"}}]}`

### `6a7da86c`
**prompt:** 거실 조명 밝기 10로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 10}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "00:10", "state": "on", "brightness": 10}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "00:10", "brightness": 10, "room": "living", "state": "on"}}]}`

### `a4ca723f`
**prompt:** 거실 조명 밝기 20로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "00:20", "state": "on", "brightness": 20}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "00:20", "brightness": 20, "room": "living", "state": "on"}}]}`

### `27b789d2`
**prompt:** 오전 11시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "11:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "23:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "23:00", "room": "living", "state": "on"}}]}`

### `eab3654c`
**prompt:** create movie mode #1. set bedroom light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "room": "bedroom", "color_temp": "warm"}}], "name": "movie"}}]}`

### `dda84793`
**prompt:** 오후 1시에 침실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "01:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "01:00", "room": "bedroom", "state": "off"}}]}`

### `8b30eb14`
**prompt:** create movie mode #6. set bedroom light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "room": "bedroom", "color_temp": "warm"}}], "name": "movie"}}]}`

### `e205fc5c`
**prompt:** 침실 조명 밝기 10로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 10}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "00:00", "state": "on", "brightness": 10}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "00:00", "brightness": 10, "room": "bedroom", "state": "on"}}]}`

### `12a4fb2e`
**prompt:** 침실 조명 밝기 20로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "23:00", "state": "on", "brightness": 20}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "23:00", "brightness": 20, "room": "bedroom", "state": "on"}}]}`

### `7215d4ff`
**prompt:** 오전 11시에 침실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "11:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "23:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "23:00", "room": "bedroom", "state": "on"}}]}`

### `88943556`
**prompt:** 오후 1시에 주방 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "01:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "01:00", "room": "kitchen", "state": "off"}}]}`

### `4d9b00bc`
**prompt:** 주방 조명 밝기 10로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 10}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "00:10", "state": "on", "brightness": 10}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "00:10", "brightness": 10, "room": "kitchen", "state": "on"}}]}`

### `1f27eeb4`
**prompt:** 주방 조명 밝기 20로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "00:00", "state": "on", "brightness": 20}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "00:00", "brightness": 20, "room": "kitchen", "state": "on"}}]}`

### `3657cacf`
**prompt:** 주방 조명 밝기 35로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 35}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "00:00", "state": "on", "brightness": 35}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "00:00", "brightness": 35, "room": "kitchen", "state": "on"}}]}`

### `c581a59d`
**prompt:** 오전 11시에 주방 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "11:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "23:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "23:00", "room": "kitchen", "state": "on"}}]}`

### `0c731771`
**prompt:** 주방 조명 밝기 80로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 80}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "00:00", "state": "on", "brightness": 80}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "00:00", "brightness": 80, "room": "kitchen", "state": "on"}}]}`

### `ca3aaa1c`
**prompt:** 오후 1시에 복도 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "hallway", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "hallway", "at": "01:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "01:00", "room": "hallway", "state": "off"}}]}`

### `6e320438`
**prompt:** 복도 조명 밝기 10로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 10}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "hallway", "at": "00:00", "state": "on", "brightness": 10}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "00:00", "brightness": 10, "room": "hallway", "state": "on"}}]}`

### `b81175e1`
**prompt:** 복도 조명 밝기 20로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "hallway", "at": "00:00", "state": "on", "brightness": 20}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "00:00", "brightness": 20, "room": "hallway", "state": "on"}}]}`
