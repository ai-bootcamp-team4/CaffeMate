# CaffeMate 고가치 평가 실행 보고

- 생성 시각: 2026-08-24T14:30:13.657Z
- Git revision: `948999ffc4f3d14d363b174c1156990b7f13a9bf`
- 통과: **35/35 (100.0%)**
- 판정 단위: case-to-automated-suite

> 각 사례는 연결된 자동화 suite의 통과 여부로 판정한다. suite 통과는 실제 사용자 연구나 운영 데이터 성능을 뜻하지 않는다.

## Suite 결과

| Suite | 결과 | 시간 | 설명 |
|---|---:|---:|---|
| area_grounding | PASS | 1814ms | 지역 식별, 공급자 장애, 상권 자료 범위 |
| proposal_finance | PASS | 2680ms | 비용 계산, 조건부 후보, 적격 후보와 순위 |
| feedback_state | PASS | 3900ms | 결과 전후 피드백과 확인 전 State 불변 |
| rag_scope | PASS | 1321ms | RAG 격리, source fence, anchor와 read-only MCP |
| document_pipeline | PASS | 1947ms | 문서 입력, 저장, 수정 가능한 반영과 재계산 |
| agent_contract | PASS | 1410ms | Agent Schema, 의미 검증, repair와 prompt 예산 |
| runtime_protocol | PASS | 3075ms | 역할 격리, 세션 수명주기, Vertex 오류와 배포 계약 |
| rag_evidence_bridge | PASS | 1521ms | 검색 hit에서 Evidence 후보와 결과 연결 |

## 사례 결과

| ID | 제목 | Suite | 결과 |
|---|---|---|---:|
| EV-001 | 동일 지명의 행정구역 모호성 | area_grounding | PASS |
| EV-002 | 중복 카페 업소 row | area_grounding | PASS |
| EV-003 | 매출과 유동인구 자료 없음 | proposal_finance | PASS |
| EV-004 | 전체 비용 범위가 자금 초과 | proposal_finance | PASS |
| EV-005 | 브랜드 비용 일부 누락 | proposal_finance | PASS |
| EV-006 | 개인 가맹 가능 여부 미확인 | proposal_finance | PASS |
| EV-007 | 직영 전용 브랜드 | proposal_finance | PASS |
| EV-008 | 평균매출 오용 | agent_contract | PASS |
| EV-009 | 참고 비용 범위로 고객 수 생성 | proposal_finance | PASS |
| EV-010 | 결과 전 피드백 요청 | feedback_state | PASS |
| EV-011 | 결과 이후 피드백 확인 전 변경 | feedback_state | PASS |
| EV-012 | 프로젝트 간 문서 유출 | rag_scope | PASS |
| EV-013 | 조회일은 최신이지만 data period는 오래됨 | rag_scope | PASS |
| EV-014 | 문서 안 Prompt Injection | document_pipeline | PASS |
| EV-015 | 문서 간 로열티 충돌 | document_pipeline | PASS |
| EV-016 | Agent output schema 실패 | agent_contract | PASS |
| EV-017 | 적격 후보 부족 | proposal_finance | PASS |
| EV-018 | 문서 변경 뒤 선택적 재계산 | document_pipeline | PASS |
| EV-019 | 자료 일부 누락 후보의 조건부 순위 | proposal_finance | PASS |
| EV-020 | OCR 자동 입력 폼과 일괄 반영 | document_pipeline | PASS |
| EV-021 | 서울 model endpoint 실패 | runtime_protocol | PASS |
| EV-022 | Proposal Agent의 존재하지 않는 브랜드 생성 | agent_contract | PASS |
| EV-023 | Evidence Assess의 과도한 사고·중복 입력 회귀 | agent_contract | PASS |
| EV-024 | Runtime 내부 검증에서 거절된 Agent 출력의 단일 repair | agent_contract | PASS |
| EV-025 | Runtime과 Control API의 Candidate Audit 의미 계약 일치 | agent_contract | PASS |
| EV-026 | Evidence Plan이 미배포 MCP connector를 호출하지 않음 | agent_contract | PASS |
| EV-027 | Intent Agent의 제한 없는 출력과 MAX_TOKENS 재시도 회귀 | agent_contract | PASS |
| EV-028 | Intent 가능성 진술과 변경 의향의 혼동 방지 | agent_contract | PASS |
| EV-029 | Typed Agent의 과도한 추론 예산 회귀 | agent_contract | PASS |
| EV-030 | 주소 공급자 지연이 Agent 연쇄 실행을 가리는 회귀 | area_grounding | PASS |
| EV-031 | Proposal Agent가 자료 부족을 빈 후보로 오인하지 않음 | agent_contract | PASS |
| EV-032 | Agent 세션 왕복 축소가 역할 격리와 삭제 보장을 깨뜨리지 않음 | runtime_protocol | PASS |
| EV-033 | 모델 semantic 출력과 Runtime 불변 envelope의 권한 분리 | agent_contract | PASS |
| EV-034 | 운영 문서 버킷과 Document Agent 경로가 설정 누락 없이 실제 왕복함 | document_pipeline | PASS |
| EV-035 | RAG 검색 hit가 Claim 근거 후보로 전달되지 않는 연결 회귀 | rag_evidence_bridge | PASS |
