# CaffeMate 구현 계획

> 상태: draft
> 갱신일: 2026-08-21

이 계획은 현재 프론트엔드 목업에서 근거 기반 완성 제품으로 이동하기 위한 구현 순서다. Slice는 병합·검증 단위이지 최종 제품 범위를 줄이는 MVP 선언이 아니다. 뒤 단계 기능을 앞 단계의 fixture로 위장하지 않는다.

## 원칙

- 한 단계는 독립적으로 검증 가능해야 한다.
- State write 권한은 API reducer 하나만 가진다.
- 돈·Gate·민감도는 결정론적 코드로 구현한다.
- Agent는 현재 State snapshot을 읽어 역할별 typed proposal을 반환하고 MCP는 read result만 반환한다. 둘 다 권위 State를 직접 쓰지 않는다.
- 외부 자료가 없으면 실패를 숨기지 않고 `UNKNOWN`, `PARTIAL`, `STALE`로 반환한다.
- 구현 완료는 테스트와 실제 read-back으로 증명한다.

## Slice 0 — 문서와 계약

### 산출물

- 제품·아키텍처·데이터·가드레일 문서
- Venture State, Evidence, Candidate Result, Agent Task·Result, role payload, 공통 typed value, MCP 10-tool JSON Schema와 manifest
- 백엔드·Agent Runtime·MCP 연결 계약
- 핵심 평가 fixture

### 완료 조건

- 모든 JSON Schema가 Python `jsonschema` draft 2020-12와 Ajv 8 strict draft 2020-12의 date/date-time format 검사를 모두 통과하며 공통 fixture 판정이 일치한다.
- fixture가 현재 제품 상태와 금지 행동을 표현한다.
- 문서 링크가 저장소 안에서 유효하다.

## Slice 1 — 인증과 프로젝트 State

### 범위

- Identity Platform 기반 로그인
- 프로젝트 목록·생성·전환
- 온보딩 네 묶음 저장
- versioned State와 single-writer reducer
- 사용자·프로젝트 접근 통제

### 완료 조건

- 로그인하지 않은 요청은 보호 화면에 접근하지 못한다.
- 프로젝트 전환 시 값이 섞이지 않는다.
- 같은 idempotency key·같은 payload는 같은 run을 반환하고, 같은 key·다른 payload는 409이며 concurrent duplicate는 run 하나만 생성한다.
- 다른 사용자의 project id를 넣어도 조회·수정되지 않는다.

## Slice 1A — Durable Workflow Core

### 범위

- `workflow_run`, `stage_run`, idempotency record와 transactional outbox
- Pub/Sub redelivery와 Worker lease·heartbeat·compare-and-swap
- 조회·event stream·cancel API
- full head 여덟 차원과 late-result 폐기

### 완료 조건

- `202` 직후 API instance를 종료해도 Worker가 run을 재개한다.
- 같은 stage redelivery에서 side effect와 checkpoint가 정확히 한 번이다.
- timeout·cancel 이후 결과는 head가 같아도 write 0이다.
- full head 각 차원을 하나씩 변경한 stale matrix의 write가 모두 0이다.

## Slice 2 — 전국 공통 상권 Evidence

### 범위

- `resolve_area`
- 주민 인구·연령
- 카페 업소 원시 관측과 중복 경고
- coverage profile과 freshness
- BigQuery ingest와 read-only MCP contract

### 완료 조건

- 행정동 이름 중복과 법정동·행정동 차이를 처리한다.
- 수치마다 기준일·출처·지리 범위를 반환한다.
- 없는 유동인구·매출을 생성하지 않는다.
- connector 실패 시 이전 값을 최신으로 표시하지 않는다.

## Slice 3 — 후보와 결정론적 판단

### 범위

- 개인카페 표준 운영 모델
- 실제 프랜차이즈 브랜드 후보
- 개인 가맹 가능 여부 Gate
- 참고 비용 범위
- 초기비용·월 고정비·손익분기·필요 주문 수
- `검토 추천·조건부 검토·제외`
- 판단 반전 조건

### 완료 조건

- 동일 입력은 동일 계산과 상태를 만든다.
- 누락값을 0이나 평균값으로 대입하지 않는다.
- 자료 일부가 없는 브랜드는 조건부로 순위에 포함된다.
- 가맹 가능 여부가 확인되지 않은 브랜드는 순위에서 제외된다.
- 평균매출을 후보 예상매출로 사용하지 않는다.

## Slice 4 — Agent Workflow

### 범위

- Evidence Research Agent
- 개인·프랜차이즈 Proposal branch
- Typed Candidate Auditor
- 서울 managed Agent Runtime과 서울 model endpoint
- deterministic root dispatcher, ADK 관리형 session create→`/api/run`→delete adapter와 full-head 검증
- Control API가 실행하는 MCP read action 경로
- schema validation, explicit repair context와 단일 repair
- Agent trace와 prompt/model version

### 완료 조건

- Agent는 State snapshot을 입력으로 사용할 수 있지만 권위 State를 직접 수정하지 못한다. 검증된 typed delta만 reducer가 반영한다.
- Agent Runtime은 MCP를 직접 호출하지 않는다.
- 모든 후보 Claim이 Evidence, 사용자 사실 또는 표시된 가정에 연결된다.
- Auditor는 누락 비용과 Hard Constraint 위반을 fixture에서 탐지한다.
- Agent 실패 시 후보·계산이 부분 commit되지 않는다.
- Agent와 model 호출이 `global` endpoint로 fallback하지 않는다.
- 서울 Runtime·승인 생성 model·embedding·reranker preflight를 독립 검증한다.

## Slice 5 — 첫 결과와 피드백

### 범위

- 현재 목업을 실제 Candidate Result Schema에 연결
- 주력 1개와 비교 후보
- 상권 근거·비용·위험·판단 반전 조건
- 결과 이후 자연어 피드백
- 변경안 확인·적용·취소
- 결과 version 비교

### 완료 조건

- 결과 전에는 피드백 입력이 보이지 않는다.
- 확인 전에는 State가 바뀌지 않는다.
- 후보 수가 부족하면 빈 자리를 부적격 후보로 채우지 않는다.
- 모바일과 데스크톱에서 근거·누락·상태가 읽힌다.

## Slice 6 — 공식 문서와 프로젝트 RAG

### 범위

- Vertex AI RAG Engine의 공식 절차·정보공개서 corpus
- project별 격리된 사용자 문서 corpus와 Cloud SQL 허용 mapping
- Document AI Layout Parser 기반 chunking
- semantic retrieval·exact typed lookup·metadata filter·rerank·원문 anchor
- 사용자 문서 upload와 project scope
- Document Analyst·자동 입력 extraction form·일괄 반영·conflict

### 완료 조건

- project filter 없는 사용자 문서 검색은 차단된다.
- 서울에서 corpus 생성·import·retrieval·rerank read-back이 모두 통과한다.
- 표 값은 헤더·단위·기준연도와 함께 반환된다.
- 숫자 Claim은 원문 page/table anchor를 가진다.
- OCR 결과는 한 폼에 자동 입력되고 사용자가 수정·삭제할 수 있다.
- 필드별 확인 없이 한 번의 일괄 반영만 요구한다.
- 불확실한 추출은 빈 값과 경고로 남고 일괄 반영 전 계산에 사용되지 않는다.

## Slice 7 — 재계산·평가·운영

### 범위

- 문서 delta에 따른 선택적 재계산
- RAG·Agent·Guardrail 평가 runner
- Cloud Run API·Worker·MCP와 managed Agent Runtime 배포
- Pub/Sub·Eventarc 비동기 처리
- 운영 추적과 비용 관찰

### 완료 조건

- 문서 숫자 하나의 변경이 관련 계산과 판단만 바꾼다.
- 이전 snapshot은 감사 이력으로 남는다.
- 금지 행동 fixture의 위반율이 0이다.
- 배포 URL, revision, image digest와 상태 확인을 read-back한다.

## 첫 end-to-end 통합 Gate

Slice 1~5가 통과하면 첫 제안 세로 흐름을 통합할 수 있다. 이는 제품 완료가 아니며 Slice 6~7의 문서 RAG·재계산·운영 품질도 현재 제품 범위에 포함된다.

## 현재 저장소와의 차이

- 현재 저장소에는 React 결과 목업과 frontend 배포만 있다.
- API·데이터베이스·MCP·Agent·RAG resource는 아직 없다.
- 생성 model id는 `gemini-3.7-flash`로 결정됐다. 실제 GCP 연결은 아직 없으므로 서울 Runtime·생성·embedding·reranker preflight가 남아 있다.
- 기존 목업의 가상 값은 제품 성능 증거로 사용하지 않는다.
