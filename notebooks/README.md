# notebooks/ — 학습 파이프라인을 바닥부터 다시 쌓아 올리는 노트북 시리즈

이 디렉토리는 Ganglion의 모델 학습 파이프라인을 **프로젝트가 실제로 자란 순서대로**
다시 밟아 보기 위한 학습용 노트북 모음이다. 목적은 코드 디테일의 이해이지
새 실험이 아니다.

## 규칙

- **노트북 하나 = 리포트의 숫자 하나 재현.** 각 노트북은 `docs/` 리포트에 기록된
  특정 수치를 목표로 잡고, 그 자리에서 다시 계산해 일치 여부를 표시한다.
  어긋나면 어디서 어긋났는지 적는다 (01의 §6 카탈로그 크기 drift가 예시).
- **라이브러리 코드를 복사하지 않는다.** 항상 `ganglion.*`을 import하고, 구현이
  궁금하면 `show()` 헬퍼(`inspect.getsource`)로 그 자리에서 연다. 구현은 패키지에,
  노트북에는 설명과 실험 결과만 둔다. 두 번째 사본이 생기면 곧 어긋난다.
- **가설 → 실행 → 숫자 → 해석** 순서를 지킨다. 리포트가 그렇게 쓰였기 때문이다.

## 실행

전용 conda 환경 `ganglion`을 쓴다. Jupyter에서는 **`Python (ganglion)`** 커널을 고른다
(노트북 메타데이터에 기본값으로 박혀 있다).

```bash
conda create -n ganglion python=3.12 -y
conda activate ganglion
pip install -e ".[dev,factory]" jupyterlab nbclient ipykernel
# 이 머신의 드라이버는 CUDA 12.8이므로 pip 기본 torch(cu130) 대신 cu128 빌드를 쓴다
pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu128
pip install "fsspec[http]<=2026.6.0"            # datasets 5.x 핀 충돌 해소
python -m ipykernel install --user --name ganglion --display-name "Python (ganglion)"
jupyter lab notebooks/
```

확인: `python -c "import torch; print(torch.cuda.is_available())"` 가 `True`,
`pytest -q` 가 전부 통과.

노트북은 첫 셀에서 `pyproject.toml`을 찾아 저장소 루트로 `chdir`하므로,
어느 디렉토리에서 커널을 띄워도 상대 경로(`examples/...`)가 동작한다.

명령줄에서 전부 다시 실행해 출력까지 갱신하려면:

```bash
jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.kernel_name=ganglion notebooks/01_contract.ipynb
```

## 시리즈 계획

| # | 노트북 | 재현 목표 | 근거 문서 | GPU / API |
|---|---|---|---|---|
| 01 | `01_contract.ipynb` — ToolSpec → Catalog → DSL/tools → parse/validate | 카탈로그 크기 1.58x/2.69x/3.40x, 데이터셋 500건 분포 | `poc_verification_report.md` §6, §14.2 | 없음 |
| 02 | Untuned Qwen3-0.6B 로컬 추론 + 메트릭 | exact 38.6% | `factory_phase2_plan.md` §10 | GPU |
| 03 | 교사 합성 → validator gate → dedup | pass rate 95.2%, 126건 | `factory_phase1_report.md` §3 | API (커밋된 synth.jsonl로 대체 가능) |
| 04 | SFT: chat template, LoRA, 학습, holdout 평가 | CUDA 86.4% | `factory_phase2_plan.md` §15.1 | GPU |
| 05 | Post-correction 규칙 + 실패 분류 | 99.2% | `factory_phase2_plan.md` §16–17 | 없음 |
| 06 | 문법 마스킹 ablation | untuned +17pp, SFT −5pp | `factory_phase2_plan.md` §12.2 | GPU |
| 07 | Self-bootstrap과 DPO가 실패한 이유 | pair yield 6% | `factory_phase2_plan.md` §13–14, `factory_bfcl_phase3_report.md` §2.6 | GPU |
| 08 | BFCL 전이 (schema compiler, per-case catalog) | macro 0.82 → 0.912 | `factory_bfcl_report.md`, `factory_bfcl_phase3_report.md` | GPU |

01과 05는 GPU 없이 돌고, 전체 이해의 절반을 차지한다.
