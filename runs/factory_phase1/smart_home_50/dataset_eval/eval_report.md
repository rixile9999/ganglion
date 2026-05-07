# Eval report — smart_home_50

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **93.0%**
- exact_match_rate:  **87.4%**
- action_match_rate: **93.0%**
- latency P50: 1483 ms
- latency P95: 2312 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 93.0% | 93.0% | 87.4% |

## Failures (63)

### `6081002b`
**prompt:** 영화 모드 scene을 만들어줘. 거실 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "living"}}], "name": "movie"}}]}`

### `9024ff85`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 거실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "living"}}], "name": "movie"}}]}`

### `6c818d85`
**prompt:** create movie mode scene. set living room light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "living"}}], "name": "movie"}}]}`

### `1bb3a0ef`
**prompt:** 영화 모드 scene을 만들어줘. 침실 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "bedroom"}}], "name": "movie"}}]}`

### `42b38e53`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 침실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "bedroom"}}], "name": "movie"}}]}`

### `609dd410`
**prompt:** create movie mode scene. set bedroom light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "bedroom"}}], "name": "movie"}}]}`

### `a145ec9f`
**prompt:** 영화 모드 scene을 만들어줘. 주방 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "kitchen"}}], "name": "movie"}}]}`

### `d97e4a6c`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 주방 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "kitchen"}}], "name": "movie"}}]}`

### `d7cbb991`
**prompt:** create movie mode scene. set kitchen light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "kitchen"}}], "name": "movie"}}]}`

### `a380d3c5`
**prompt:** 영화 모드 scene을 만들어줘. 복도 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "hallway"}}], "name": "movie"}}]}`

### `734c49c4`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 복도 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "hallway"}}], "name": "movie"}}]}`

### `d641e9d3`
**prompt:** create movie mode scene. set hallway light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "hallway"}}], "name": "movie"}}]}`

### `bc0fe090`
**prompt:** 오후 8시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "20:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "16:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "16:00", "room": "living", "state": "off"}}]}`

### `68e22221`
**prompt:** 영화 모드 scene을 만들어줘. 서재 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "office"}}], "name": "movie"}}]}`

### `10ebb4ac`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 서재 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "office"}}], "name": "movie"}}]}`

### `ae5aa9a6`
**prompt:** 오후 9시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "21:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "17:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "17:00", "room": "living", "state": "off"}}]}`

### `cff59ec9`
**prompt:** create movie mode scene. set office light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "office"}}], "name": "movie"}}]}`

### `e6c20aa7`
**prompt:** 오후 10시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "22:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "10:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "10:00", "room": "living", "state": "off"}}]}`

### `a34f7716`
**prompt:** 영화 모드 #0 만들어줘. 거실 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "living"}}], "name": "movie"}}]}`

### `f42c50ea`
**prompt:** create movie mode #0. set living room light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** set_light.state is required
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "living"}}], "name": "movie"}}]}`
