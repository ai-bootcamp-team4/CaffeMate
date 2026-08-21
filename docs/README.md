# CaffeMate 개발 문서

> 상태: active
> 갱신일: 2026-08-21

이 디렉터리는 CaffeMate 구현의 기준 문서다. 제품 탐색 기록과 조사 전문을 그대로 복사하지 않고, 개발자가 구현·검증하는 데 필요한 요구사항과 계약만 유지한다.

## 먼저 읽기

1. [제품 명세](./product-spec.md)
2. [Agent·RAG 런타임 상세 계약](./product-very-spec.md)
3. [시스템 아키텍처](./architecture/system-architecture.md)
4. [상태와 워크플로](./architecture/state-and-workflows.md)
5. [의사결정 모델](./architecture/decision-model.md)
6. [데이터와 그라운딩](./architecture/data-and-grounding.md)
7. [에이전트와 MCP](./architecture/agent-and-mcp.md)
8. [가드레일](./architecture/guardrails.md)
9. [평가 계획](./evaluation/evaluation-plan.md)
10. [구현 계획](./implementation-plan.md)
11. [프론트엔드 배포](./deployment.md)

## 문서 권한

- 이 저장소는 개발 source of truth다.
- [제품 명세](./product-spec.md)의 `CONFIRMED` 항목은 구현 요구사항이다.
- [Agent·RAG 런타임 상세 계약](./product-very-spec.md)은 제품 명세를 구현하는 현재 기술 계약이며 제품 행동을 바꿀 수 없다.
- `PROVISIONAL`은 검증 가능한 기본값이며, 구현 중 쉽게 교체할 수 있게 둔다.
- `PENDING`은 임의로 확정하지 않는다.
- JSON Schema는 프론트엔드·백엔드·에이전트 사이의 기계 검증 계약이다.
- 문서끼리 충돌하면 구현을 진행하지 않고 충돌을 기록한 뒤 `product-spec.md`와 계약 파일을 먼저 맞춘다.

## 현재 구현 상태

| 영역 | 상태 | 설명 |
| --- | --- | --- |
| React 결과 목업 | 구현됨 | 가상 프랜차이즈 결과와 결과 이후 피드백 UI |
| Frontend Cloud Run | 배포됨 | 현재 저장소의 `cloudbuild.yaml`과 `docs/deployment.md` 범위 |
| 로그인·프로젝트 State | 설계됨 | 아직 구현되지 않음 |
| 상권·프랜차이즈 데이터 | 설계·실증 중 | 전국 공통 자료와 지역별 자료의 품질이 다름 |
| API·MCP·Agent·RAG | 설계됨 | 아직 생성·배포되지 않음 |
| 문서 분석·재계산 | 후속 세로 흐름 | 첫 제안 MVP 이후 구현 |

## 계약 파일

- [Venture State Schema](./contracts/venture-state.schema.json)
- [Evidence Record Schema](./contracts/evidence-record.schema.json)
- [Candidate Result Schema](./contracts/candidate-result.schema.json)
- [Document Extraction Form Schema](./contracts/document-extraction-form.schema.json)

스키마가 바뀌면 관련 예시·평가 fixture·API 타입을 같은 변경에서 갱신한다.

## 포함하지 않는 기록

- 밤샘 작업 로그와 에이전트 활동 내역
- 거절된 과거 MVP 전문
- 경쟁사 조사 원문 전체
- 로컬 Obsidian에서만 작동하는 링크
- 근거가 확인되지 않은 업계 관행 숫자

## 변경 규칙

1. 제품 행동 변경은 `product-spec.md`에 먼저 반영한다.
2. 입력·출력 구조 변경은 JSON Schema를 함께 갱신한다.
3. 실패 방식 변경은 `guardrails.md`와 평가 fixture를 함께 갱신한다.
4. 배포 상태는 실제 운영 read-back 없이 완료로 표시하지 않는다.
5. 문서가 구현보다 앞서거나 뒤처졌다면 현재 상태를 명시한다.
