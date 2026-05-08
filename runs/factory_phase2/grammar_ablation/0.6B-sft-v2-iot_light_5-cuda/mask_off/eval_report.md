# Eval report — iot_light_5

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **95.6%**
- exact_match_rate:  **86.4%**
- action_match_rate: **95.4%**
- latency P50: 1404 ms
- latency P95: 2514 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 95.6% | 95.4% | 86.4% |

## Failures (68)

### `a35739fc`
**prompt:** 오전 1시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "01:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "08:00", "room": "living", "state": "on"}}]}`

### `a05f993e`
**prompt:** 오후 1시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "13:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "23:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "23:00", "room": "living", "state": "off"}}]}`

### `8b29fc1e`
**prompt:** 오전 2시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "02:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "08:00", "room": "living", "state": "on"}}]}`

### `5743d282`
**prompt:** 오전 3시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "03:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "07:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "07:00", "room": "living", "state": "on"}}]}`

### `ada16639`
**prompt:** 오후 3시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "15:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "21:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "21:00", "room": "living", "state": "off"}}]}`

### `f087962c`
**prompt:** 오후 4시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "16:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "20:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "20:00", "room": "living", "state": "off"}}]}`

### `9c5c7545`
**prompt:** 조명 장치 목록 보여줘 #8
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"id": "8"}}]}`

### `2b798b99`
**prompt:** 오후 5시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "17:00", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "21:00", "state": "off"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "21:00", "room": "living", "state": "off"}}]}`

### `1fbf6e76`
**prompt:** 등록된 조명 디바이스 목록 보여줘 #10
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"id": "10"}}]}`

### `bd9beb8d`
**prompt:** list devices #12
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"count": 12}}]}`

### `f972b842`
**prompt:** list light devices #13
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"devices": "13"}}]}`

### `3cd23428`
**prompt:** 조명 장치 목록 보여줘 #16
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"id": "16"}}]}`

### `10ebb4ac`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 서재 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "office", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "hallway", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**raw:** `{"calls": [{"action": "create_scene", "args": {"actions": [{"action": "set_light", "args": {"brightness": 20, "color_temp": "warm", "room": "hallway", "state": "on"}}], "name": "movie"}}]}`

### `5b4011a5`
**prompt:** 등록된 조명 디바이스 목록 보여줘 #18
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"id": "18"}}]}`

### `64737bce`
**prompt:** 오전 10시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "10:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "08:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "08:00", "room": "living", "state": "on"}}]}`

### `f0c0d521`
**prompt:** list devices #20
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"count": 20}}]}`

### `27b789d2`
**prompt:** 오전 11시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "11:00", "state": "on"}}]}`
**predicted:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "09:00", "state": "on"}}]}`
**raw:** `{"calls": [{"action": "schedule_light", "args": {"at": "09:00", "room": "living", "state": "on"}}]}`

### `75cd946c`
**prompt:** list light devices #21
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"devices": "21"}}]}`

### `87e4c85f`
**prompt:** show available devices #22
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"device_id": "22"}}]}`

### `ed8d327a`
**prompt:** 조명 장치 목록 보여줘 #24
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls": [{"action": "list_devices", "args": {"count": 24}}]}`
