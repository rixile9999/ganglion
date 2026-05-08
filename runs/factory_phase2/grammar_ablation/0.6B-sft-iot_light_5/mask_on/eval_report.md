# Eval report — iot_light_5

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **94.0%**
- exact_match_rate:  **68.6%**
- action_match_rate: **94.0%**
- latency P50: 1610 ms
- latency P95: 2615 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 94.0% | 94.0% | 68.6% |

## Failures (157)

### `9c7c8e4f`
**prompt:** 거실 불 70%로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 70}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "brightness": 70}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "brightness": 70}}]}`

### `8470b798`
**prompt:** 서재 조명을 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "on", "color_temp": "warm"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "on"}}]}`

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
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "brightness": 20, "color_temp": "warm"}}]}}]}`

### `a35739fc`
**prompt:** 오전 1시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "01:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`

### `9024ff85`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 거실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "brightness": 20, "color_temp": "warm"}}]}}]}`

### `6c818d85`
**prompt:** create movie mode scene. set living room light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "color_temp": "warm", "brightness": 20}}]}}]}`

### `8b29fc1e`
**prompt:** 오전 2시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "02:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`

### `00f13c65`
**prompt:** movie scene for living room light, 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "color_temp": "warm", "brightness": 20}}]}}]}`

### `5743d282`
**prompt:** 오전 3시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "03:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`

### `42b38e53`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 침실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "brightness": 20, "color_temp": "warm"}}]}}]}`

### `78de6204`
**prompt:** 거실 불 10%로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 10}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "brightness": 100}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "brightness": 100}}]}`

### `609dd410`
**prompt:** create movie mode scene. set bedroom light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "color_temp": "warm", "brightness": 20}}]}}]}`

### `6a7da86c`
**prompt:** 거실 조명 밝기 10로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 10}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 100}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 100}}]}`

### `9c44ac95`
**prompt:** 오전 4시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "04:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`

### `2b6e86e3`
**prompt:** movie scene for bedroom light, 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "color_temp": "warm", "brightness": 20}}]}}]}`

### `83bcbaa5`
**prompt:** set living room light to 10%
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 10}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 100}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 100}}]}`

### `a145ec9f`
**prompt:** 영화 모드 scene을 만들어줘. 주방 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "brightness": 20, "color_temp": "warm"}}]}}]}`

### `6608f331`
**prompt:** 거실 불 20%로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "brightness": 20}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "brightness": 20}}]}`

### `7d60ea4a`
**prompt:** 오전 5시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "05:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
