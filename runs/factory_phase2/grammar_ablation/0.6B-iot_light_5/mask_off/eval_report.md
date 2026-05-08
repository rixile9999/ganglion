# Eval report — iot_light_5

- train: 0
- holdout: 500

## Headline metrics

- syntax_valid_rate: **66.0%**
- exact_match_rate:  **40.8%**
- action_match_rate: **65.6%**
- latency P50: 682 ms
- latency P95: 1194 ms

## Per-strategy breakdown

| strategy | n | syntax | action | exact |
|---|---|---|---|---|
| dataset.jsonl | 500 | 66.0% | 65.6% | 40.8% |

## Failures (296)

### `83bb30f2`
**prompt:** 조명 장치 목록 보여줘
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** invalid JSON: Expecting ',' delimiter
**raw:** `{"calls":[{"action":"list_devices,args":{"}}]}`

### `8470b798`
**prompt:** 서재 조명을 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "office", "state": "on", "color_temp": "warm"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 80, "color_temp": "warm"}}]}`
**raw:** `{"calls":[{"action":"set_light","args":{"room":"kitchen","state":"on","brightness":80,"color_temp":"warm"}}]}`

### `7df47071`
**prompt:** 복도 불 꺼줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "hallway", "state": "off"}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "living", "state": "off"}}]}`
**raw:** `{"calls":[{"action":"set_light","args":{"room":"living","state":"off"}}]}`

### `6fc284f6`
**prompt:** 침실 불 밝기 35로 켜줘
**expected:** `{"calls": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 35}}]}`
**predicted:** `{"calls": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 35}}]}`
**raw:** `{"calls":[{"action":"set_light","args":{"room":"kitchen","state":"on","brightness":35}}]}`

### `c233229d`
**prompt:** 오후 6시에 복도 조명 켜줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "hallway", "at": "18:00", "state": "on"}}]}`
**predicted:** *(parse failed)*
**error:** schedule_light.state is required
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"hallway","at":"18:00"}}]}`

### `a35739fc`
**prompt:** 오전 1시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "01:00", "state": "on"}}]}`
**predicted:** *(parse failed)*
**error:** schedule_light.state is required
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"living","at":"08:00"}}]}`

### `688b1459`
**prompt:** 사용 가능한 조명 장치 목록 알려줘
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls":[{"action":"list_devices","args":{"room":"living"}}]}`

### `a05f993e`
**prompt:** 오후 1시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "13:00", "state": "off"}}]}`
**predicted:** *(parse failed)*
**error:** schedule_light.state is required
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"bedroom","at":"01:00"}}]}`

### `62d77875`
**prompt:** 등록된 조명 디바이스 목록 보여줘
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** invalid JSON: Expecting ',' delimiter
**raw:** `{"calls":[{"action":"list_devices,args":{"}}]}`

### `8b29fc1e`
**prompt:** 오전 2시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "02:00", "state": "on"}}]}`
**predicted:** *(parse failed)*
**error:** schedule_light.state is required
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"living","at":"08:00"}}]}`

### `5902979a`
**prompt:** 집 안 조명 장치 목록 확인해줘
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** list_devices does not accept args
**raw:** `{"calls":[{"action":"list_devices","args":{"room":"home"}}]}`

### `3730913f`
**prompt:** 오후 2시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "14:00", "state": "off"}}]}`
**predicted:** *(parse failed)*
**error:** at must be HH:MM
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"living","at":"2:00"}}]}`

### `4ae46322`
**prompt:** list devices
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** invalid JSON: Expecting ':' delimiter
**raw:** `{"calls":[{"action":"list_devices","args{}"}]}`

### `1bb3a0ef`
**prompt:** 영화 모드 scene을 만들어줘. 침실 조명은 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "kitchen", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**raw:** `{"calls":[{"action":"create_scene","args":{"name":"movie","actions":[{"action":"set_light","args":{"room":"kitchen","state":"on","brightness":20,"color_temp":"warm"}}]}}]}`

### `5743d282`
**prompt:** 오전 3시에 거실 불 켜지게 예약해줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "03:00", "state": "on"}}]}`
**predicted:** *(parse failed)*
**error:** schedule_light.state is required
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"living","at":"09:00"}}]}`

### `145f87b4`
**prompt:** list light devices
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** invalid JSON: Expecting ':' delimiter
**raw:** `{"calls":[{"action":"list_devices","args{}"}]}`

### `42b38e53`
**prompt:** 영화 볼 때 쓸 scene 만들어줘. 침실 조명 20% 따뜻하게 켜줘
**expected:** `{"calls": [{"action": "create_scene", "args": {"name": "movie", "actions": [{"action": "set_light", "args": {"room": "bedroom", "state": "on", "brightness": 20, "color_temp": "warm"}}]}}]}`
**predicted:** *(parse failed)*
**error:** invalid JSON: Expecting value
**raw:** ````json
{"calls":[{"action":"create_scene","args":{"name":"movie","actions":[{"action":"set_light","args":{"room":"kitchen","state":"on","brightness":20,"color_temp":"warm"}]}]}}]}
````

### `ada16639`
**prompt:** 오후 3시에 거실 조명 꺼줘
**expected:** `{"calls": [{"action": "schedule_light", "args": {"room": "living", "at": "15:00", "state": "off"}}]}`
**predicted:** *(parse failed)*
**error:** schedule_light.state is required
**raw:** `{"calls":[{"action":"schedule_light","args":{"room":"bedroom","at":"21:00"}}]}`

### `884b5f8d`
**prompt:** 현재 침실 조명 상태 확인해줘
**expected:** `{"calls": [{"action": "get_light_state", "args": {"room": "bedroom"}}]}`
**predicted:** `{"calls": [{"action": "get_light_state", "args": {"room": "kitchen"}}]}`
**raw:** `{"calls":[{"action":"get_light_state","args":{"room":"kitchen"}}]}`

### `64d20be1`
**prompt:** show available devices
**expected:** `{"calls": [{"action": "list_devices", "args": {}}]}`
**predicted:** *(parse failed)*
**error:** invalid JSON: Expecting ':' delimiter
**raw:** `{"calls":[{"action":"list_devices","args{}"}]}`
