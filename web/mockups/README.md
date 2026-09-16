# 운영 콘솔 GUI 시안 (2026-09-15)

[`docs/refine_proposal.md`](../../docs/refine_proposal.md)에서 설계한 화면을 정적 하이파이
목업으로 그린 아트보드 6장이다. 기존 `web/` 콘솔의 디자인 시스템(warm phosphor 팔레트,
IBM Plex Mono / Sans Condensed, `web/assets/style.css`의 토큰과 컴포넌트 값)을 그대로
이어받았다. 화면의 수치·ID는 화면 간에 서로 맞춘 예시값이다.

디자인 캔버스(편집·PNG/PDF 내보내기): https://claude.ai/artifact/4nXiBVVuxohw87AwkYKVc3

| 파일 | 화면 | refine.md 항목 |
|---|---|---|
| `Main.dc.html` | 09 Chat — 프롬프트 → 순서 있는 툴콜 목록 → F⁰/Fᴷ 나란히 보기 → 판정 바 | R1.0 / R1.2 / R2.1 |
| `ModelPicker.dc.html` | 모델 선택 팝오버 — `configs/models.yaml` 레지스트리, 지문 불일치·no seed 배지 | R1.0 |
| `Catalog.dc.html` | 02 Catalog — ORIGINAL ⇄ ACTION IR ⇄ CANONICALISATION 3패널 | R1.1 |
| `Labels.dc.html` | 05 Label Queue — wrong only / changed 필터, 키보드 라벨링, LabelRecord | R2.1 (+R3 필터) |
| `Rules.dc.html` | 06 Rule Lifecycle — 확장(블라인드 결정) / 축소(F⁰/Fᴷ 귀속 → retire 게이트) | R2.2 |
| `Loops.dc.html` | 10 Loops — 런 매니페스트 레일, KPI, 실패형 히스토그램, 전이 행렬, 학습 provenance | R3 |

`canvas.json`은 캔버스 위 배치와 메모다. 각 `.dc.html`은 Design Component 형식이지만
정적 마크업만 담고 있어 브라우저로 직접 열어도 화면이 그대로 보인다(`support.js`
참조는 캔버스 런타임용이며 없어도 무방).
