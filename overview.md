# 목표
tool calling(function calling)에 최적화 되어 있는 에이전트용 도구(프레임워크) 개발

# 필요성
- 효율적인 MCP 개발 및 활용 목적
- 정확한 함수호출을 위한 스키마 제공에 있어 도구의 목록이 늘어날 수록, 즉 호출 가능한 함수
목록이 늘어날 수록 토큰소모량이 크게 증가
- LLM의 반응속도역시 느려질걸로 예상

더 자세한 시장 조사 및 기술 조사 필요 (레퍼런스 추가할것)

# 구현 아이디어
tool calling을 대체할 tool calling보다 느슨한 DSL 정의.
DSL을 문법적으로 파싱하여 tool calling 코드로 전환.
LORA, fine tuning, 모델 증류등의 기법을 통하여 범용 언어 모델을 target DSL에 최적화(편향)

## DSL => tool calling 매핑 프로세스 정의
- 자연어 프롬프트(function call 필요성 감지) -> reflexive language model 호출(RLM) -> DSL -> function call 순서로 변환
- 기본적으로 EBNF 이용: 파싱 결과물이 function call with parameters가 되는 형
- LLM 출력 결과물이 틀린 형식의 DSL일 경우 대응전략
    1. syntax analysis: 문법적으로 틀린부분이 명확하다면 에러 메시지와 함께 다시 LLM입력값으로 전달하여 보정시도 
    2. 틀린 패턴 분석: 틀리는 패턴이 한정적이라면 해당 패턴을 파싱할수 있도록 EBNF 규칙 확장

## 기술 확장 전환
위 프로세스를 ML-Ops로 확장. 임의의 주어진 tool schema에 대하여 DSL 정의 및 소형 모델 튜닝, 최적화 작업을 자동화 시키는 프레임워크로 기술 확장

# POC 예시
 1. 자동 조명등의 IOT 분야
 2. 코인 트레이딩 분야
 3. 널리 알려진 function calling 벤치마크
