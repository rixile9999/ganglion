# Eval report — iot_light_5

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **92.0%**
- exact_match_rate:  **73.4%**
- action_match_rate: **92.0%**
- latency P50: 1601 ms
- latency P95: 2463 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 92.0% | 92.0% | 73.4% |

## Failures (133)

### `7df47071`
**prompt:** 복도 불 꺼줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "hallway", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "off"}}]}`

### `6081002b`
**prompt:** 영화 모드 scene을 만들어줘. 거실 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "living", "brightness": 20, "color_temp": "warm"}}], "name": "movie"}}]}`

### `a35739fc`
**prompt:** 오전 1시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "01:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "09:00", "room": "living", "state": "on"}}]}`

### `9024ff85`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 거실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "living", "brightness": 20, "color_temp": "warm"}}], "name": "movie"}}]}`

### `a05f993e`
**prompt:** 오후 1시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "12:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "12:00", "room": "living", "state": "off"}}]}`

### `8b29fc1e`
**prompt:** 오전 2시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "02:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "08:00", "room": "living", "state": "on"}}]}`

### `00f13c65`
**prompt:** movie scene for living room light, 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "living", "color_temp": "warm", "brightness": 20}}], "name": "movie"}}]}`

### `3730913f`
**prompt:** 오후 2시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "14:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "16:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "16:00", "room": "living", "state": "off"}}]}`

### `1bb3a0ef`
**prompt:** 영화 모드 scene을 만들어줘. 침실 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "bedroom", "brightness": 20, "color_temp": "warm"}}], "name": "movie"}}]}`

### `5743d282`
**prompt:** 오전 3시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "03:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "09:00", "room": "living", "state": "on"}}]}`

### `42b38e53`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 침실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "bedroom", "brightness": 20, "color_temp": "warm"}}], "name": "movie"}}]}`

### `609dd410`
**prompt:** create movie mode scene. set bedroom light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "bedroom", "color_temp": "warm", "brightness": 20}}], "name": "movie"}}]}`

### `9c44ac95`
**prompt:** 오전 4시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "04:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "08:00", "room": "living", "state": "on"}}]}`

### `2b6e86e3`
**prompt:** movie scene for bedroom light, 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "bedroom", "color_temp": "warm", "brightness": 20}}], "name": "movie"}}]}`

### `a145ec9f`
**prompt:** 영화 모드 scene을 만들어줘. 주방 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "kitchen", "brightness": 20, "color_temp": "warm"}}], "name": "movie"}}]}`

### `7d60ea4a`
**prompt:** 오전 5시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "05:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "09:00", "room": "living", "state": "on"}}]}`

### `d97e4a6c`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 주방 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "kitchen", "brightness": 20, "color_temp": "warm"}}], "name": "movie"}}]}`

### `a380d3c5`
**prompt:** 영화 모드 scene을 만들어줘. 복도 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "hall", "brightness": 20, "color_temp": "warm"}}], "name": "movie"}}]}`

### `734c49c4`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 복도 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "hallway", "brightness": 20, "color_temp": "warm"}}], "name": "movie"}}]}`

### `2d11094c`
**prompt:** movie scene for hallway light, 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"room": "hallway", "color_temp": "warm", "brightness": 20}}], "name": "movie"}}]}`
