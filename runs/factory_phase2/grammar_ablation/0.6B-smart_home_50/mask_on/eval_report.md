# Eval report — smart_home_50

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **100.0%**
- exact_match_rate:  **52.6%**
- action_match_rate: **99.4%**
- latency P50: 1049 ms
- latency P95: 1568 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 100.0% | 99.4% | 52.6% |

## Failures (237)

### `7f39edf8`
**prompt:** 밤 10시 반에 침실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "22:30", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "22:00", "state": "off"}}]}`
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"bedroom","at":"22:00","state":"off"}}]}`

### `8470b798`
**prompt:** 서재 조명을 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "on", "color_temp": "warm"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 80}}]}`
**raw:** `{"calls":[{"action":"set_light","args":{"room":"kitchen","state":"on","brightness":80}}]}`

### `7df47071`
**prompt:** 복도 불 꺼줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "hallway", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "off"}}]}`
**raw:** `{"calls":[{"action":"set_light","args":{"room":"kitchen","state":"off"}}]}`

### `6fc284f6`
**prompt:** 침실 불 밝기 35로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 35}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 35}}]}`
**raw:** `{"calls":[{"action":"set_light","args":{"room":"kitchen","state":"on","brightness":35}}]}`

### `c233229d`
**prompt:** 오후 6시에 복도 조명 켜줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "hallway", "at": "18:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "hallway", "state": "on"}}]}`
**raw:** `{"calls":[{"action":"set_light","args":{"room":"hallway","state":"on"}}]}`

### `a35739fc`
**prompt:** 오전 1시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "01:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"kitchen","at":"09:00","state":"on"}}]}`

### `9024ff85`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 거실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}, {"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls":[{"action":"create_scene","args":{"name":"movie","actions":[{"action":"set_light","args":{"room":"living","state":"on","brightness":20,"color_temp":"warm"}},{"action":"set_light","args":{"roo`

### `a05f993e`
**prompt:** 오후 1시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "21:00", "state": "off"}}]}`
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"living","at":"21:00","state":"off"}}]}`

### `8b29fc1e`
**prompt:** 오전 2시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "02:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"kitchen","at":"08:00","state":"on"}}]}`

### `3730913f`
**prompt:** 오후 2시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "14:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "22:00", "state": "off"}}]}`
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"kitchen","at":"22:00","state":"off"}}]}`

### `1bb3a0ef`
**prompt:** 영화 모드 scene을 만들어줘. 침실 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20}}]}}]}`
**raw:** `{"calls":[{"action":"create_scene","args":{"name":"movie","actions":[{"action":"set_light","args":{"room":"kitchen","state":"on","brightness":20}}]}}]}`

### `5743d282`
**prompt:** 오전 3시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "03:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"kitchen","at":"09:00","state":"on"}}]}`

### `42b38e53`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 침실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}, {"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**raw:** `{"calls":[{"action":"create_scene","args":{"name":"movie","actions":[{"action":"set_light","args":{"room":"kitchen","state":"on","brightness":20,"color_temp":"warm"}},{"action":"set_light","args":{"ro`

### `ada16639`
**prompt:** 오후 3시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "15:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "21:00", "state": "off"}}]}`
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"kitchen","at":"21:00","state":"off"}}]}`

### `884b5f8d`
**prompt:** 현재 침실 조명 상태 확인해줘
**expected:** `{"calls": [{"action": "get_light_state", "args": {"room": "bedroom"}}]}`
**predicted:** `{"calls": [{"action": "get_light_state", "args": {"room": "kitchen"}}]}`
**raw:** `{"calls":[{"action":"get_light_state","args":{"room":"kitchen"}}]}`

### `609dd410`
**prompt:** create movie mode scene. set bedroom light to 20% warm
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}, {"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**raw:** `{"calls":[{"action":"create_scene","args":{"name":"movie","actions":[{"action":"set_light","args":{"room":"bedroom","state":"on","brightness":20,"color_temp":"warm"}},{"action":"set_light","args":{"ro`

### `9c44ac95`
**prompt:** 오전 4시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "04:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "04:00", "state": "on"}}]}`
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"kitchen","at":"04:00","state":"on"}}]}`

### `7409138a`
**prompt:** 침실 불 상태 알려줘
**expected:** `{"calls": [{"action": "get_light_state", "args": {"room": "bedroom"}}]}`
**predicted:** `{"calls": [{"action": "get_light_state", "args": {"room": "kitchen"}}]}`
**raw:** `{"calls":[{"action":"get_light_state","args":{"room":"kitchen"}}]}`

### `83bcbaa5`
**prompt:** set living room light to 10%
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 10}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off", "brightness": 10}}]}`
**raw:** `{"calls":[{"action":"set_light","args":{"room":"living","state":"off","brightness":10}}]}`

### `f087962c`
**prompt:** 오후 4시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "16:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "kitchen", "at": "20:00", "state": "off"}}]}`
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"kitchen","at":"20:00","state":"off"}}]}`
