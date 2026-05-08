# Eval report — iot_light_5

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **99.0%**
- exact_match_rate:  **76.6%**
- action_match_rate: **99.0%**
- latency P50: 1524 ms
- latency P95: 2391 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 99.0% | 99.0% | 76.6% |

## Failures (117)

### `7df47071`
**prompt:** 복도 불 꺼줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "hallway", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "off"}}]}`

### `6081002b`
**prompt:** 영화 모드 scene을 만들어줘. 거실 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}], "name": "movie"}}]}`

### `a35739fc`
**prompt:** 오전 1시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "01:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "06:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "06:00", "room": "living", "state": "on"}}]}`

### `9024ff85`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 거실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}], "name": "movie"}}]}`

### `a05f993e`
**prompt:** 오후 1시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "22:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "22:00", "room": "living", "state": "off"}}]}`

### `6c818d85`
**prompt:** create movie mode scene. set living room light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "color_temp": "warm"}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "color_temp": "warm"}}], "name": "movie"}}]}`

### `8b29fc1e`
**prompt:** 오전 2시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "02:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "08:00", "room": "living", "state": "on"}}]}`

### `3730913f`
**prompt:** 오후 2시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "14:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "22:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "22:00", "room": "living", "state": "off"}}]}`

### `1bb3a0ef`
**prompt:** 영화 모드 scene을 만들어줘. 침실 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20}}], "name": "movie"}}]}`

### `5743d282`
**prompt:** 오전 3시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "03:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "09:00", "room": "living", "state": "on"}}]}`

### `42b38e53`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 침실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20}}], "name": "movie"}}]}`

### `ada16639`
**prompt:** 오후 3시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "15:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "19:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "19:00", "room": "living", "state": "off"}}]}`

### `9c44ac95`
**prompt:** 오전 4시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "04:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "06:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "06:00", "room": "living", "state": "on"}}]}`

### `a145ec9f`
**prompt:** 영화 모드 scene을 만들어줘. 주방 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20}}], "name": "movie"}}]}`

### `7d60ea4a`
**prompt:** 오전 5시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "05:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "07:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "07:00", "room": "living", "state": "on"}}]}`

### `d97e4a6c`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 주방 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20}}], "name": "movie"}}]}`

### `2b798b99`
**prompt:** 오후 5시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "17:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "19:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "19:00", "room": "living", "state": "off"}}]}`

### `d7cbb991`
**prompt:** create movie mode scene. set kitchen light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "color_temp": "warm"}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "color_temp": "warm"}}], "name": "movie"}}]}`

### `a380d3c5`
**prompt:** 영화 모드 scene을 만들어줘. 복도 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20}}], "name": "movie"}}]}`

### `734c49c4`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 복도 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20}}], "name": "movie"}}]}`
