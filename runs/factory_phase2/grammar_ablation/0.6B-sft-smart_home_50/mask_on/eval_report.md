# Eval report — smart_home_50

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **99.8%**
- exact_match_rate:  **70.8%**
- action_match_rate: **90.6%**
- latency P50: 1929 ms
- latency P95: 3121 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 99.8% | 90.6% | 70.8% |

## Failures (146)

### `8470b798`
**prompt:** 서재 조명을 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "on", "color_temp": "warm"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "on"}}]}`

### `7df47071`
**prompt:** 복도 불 꺼줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "hallway", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "set_fan", "args": {"room": "office", "speed": 0, "state": "off"}}]}`
**raw:** `{"calls": [{"action": "set_fan", "args": {"room": "office", "speed": 0, "state": "off"}}]}`

### `a35739fc`
**prompt:** 오전 1시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "01:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`

### `a05f993e`
**prompt:** 오후 1시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:00", "state": "off"}}]}`

### `8b29fc1e`
**prompt:** 오전 2시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "02:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`

### `3730913f`
**prompt:** 오후 2시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "14:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "22:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "22:00", "state": "off"}}]}`

### `5743d282`
**prompt:** 오전 3시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "03:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`

### `ada16639`
**prompt:** 오후 3시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "15:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:00", "state": "off"}}]}`

### `f087962c`
**prompt:** 오후 4시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "16:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:40", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:40", "state": "off"}}]}`

### `a145ec9f`
**prompt:** 영화 모드 scene을 만들어줘. 주방 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20}}]}}, {"action": "get_light_state", "args": {"room": "kitchen"}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20}}]}}, {"action": "get_light_state", "arg`

### `d97e4a6c`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 주방 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}]}}, {"action": "start_camera_recording", "args": {"location": "living"}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}]}}, {"action": "start_camera_recording"`

### `2b798b99`
**prompt:** 오후 5시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "17:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:00", "state": "off"}}]}`

### `8cfe7814`
**prompt:** 오후 6시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "18:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "18:00", "state": "off"}}]}`

### `a380d3c5`
**prompt:** 영화 모드 scene을 만들어줘. 복도 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20}}]}}, {"action": "set_light", "args": {"room": "living", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20}}]}}, {"action": "set_light", "args": {"`

### `734c49c4`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 복도 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}]}}, {"action": "start_camera_recording", "args": {"location": "living"}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20}}]}}, {"action": "start_camera_recording"`

### `02ea9d7a`
**prompt:** 오후 7시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "19:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "19:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "19:00", "state": "off"}}]}`

### `bc0fe090`
**prompt:** 오후 8시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "20:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "20:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "20:00", "state": "off"}}]}`

### `68e22221`
**prompt:** 영화 모드 scene을 만들어줘. 서재 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`

### `10ebb4ac`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 서재 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "living", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`

### `ae5aa9a6`
**prompt:** 오후 9시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "21:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "19:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"room": "bedroom", "at": "19:00", "state": "off"}}]}`
