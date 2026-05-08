# Eval report — iot_light_5

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **99.4%**
- exact_match_rate:  **86.0%**
- action_match_rate: **99.2%**
- latency P50: 1439 ms
- latency P95: 2581 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 99.4% | 99.2% | 86.0% |

## Failures (70)

### `a35739fc`
**prompt:** 오전 1시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "01:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`

### `a05f993e`
**prompt:** 오후 1시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "23:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "23:00", "state": "off"}}]}`

### `8b29fc1e`
**prompt:** 오전 2시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "02:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`

### `5743d282`
**prompt:** 오전 3시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "03:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "07:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "07:00", "state": "on"}}]}`

### `ada16639`
**prompt:** 오후 3시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "15:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "21:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "21:00", "state": "off"}}]}`

### `f087962c`
**prompt:** 오후 4시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "16:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "20:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "20:00", "state": "off"}}]}`

### `2b798b99`
**prompt:** 오후 5시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "17:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "21:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "21:00", "state": "off"}}]}`

### `d641e9d3`
**prompt:** create movie mode scene. set hallway light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** invalid JSON: Unterminated string starting at
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "hallway", "state": "on"}}]}}, {"action": "`

### `10ebb4ac`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 서재 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "hallway", "state": "on"}}]}}]}`

### `64737bce`
**prompt:** 오전 10시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "10:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`

### `27b789d2`
**prompt:** 오전 11시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "11:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`

### `5f6941eb`
**prompt:** 영화 모드 #3 만들어줘. 복도 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** invalid JSON: Unterminated string starting at
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "hallway", "state": "on"}}]}}, {"action": "`

### `a07372e8`
**prompt:** 거실 조명을 중립으로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "color_temp": "neutral"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}`

### `c65d0588`
**prompt:** make living room light neutral
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "color_temp": "neutral"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on"}}]}`

### `6e7cbcf5`
**prompt:** 거실 조명을 차갑게 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "color_temp": "cool"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "color_temp": "cool"}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "color_temp": "cool"}}]}`

### `bd874de5`
**prompt:** 오전 1시에 침실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "01:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "08:00", "state": "on"}}]}`

### `dda84793`
**prompt:** 오후 1시에 침실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "23:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "23:00", "state": "off"}}]}`

### `d31df063`
**prompt:** 오전 2시에 침실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "02:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "08:00", "state": "on"}}]}`

### `10d21cef`
**prompt:** 오전 3시에 침실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "03:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "07:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "07:00", "state": "on"}}]}`

### `3bb13222`
**prompt:** 영화 모드 #8 만들어줘. 복도 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** invalid JSON: Unterminated string starting at
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "hallway", "state": "on"}}]}}, {"action": "`
