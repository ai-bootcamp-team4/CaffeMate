# 평가 계획

> 상태: draft
> 갱신일: 2026-08-21

## 목표

좋아 보이는 답을 평가하는 것이 아니라 잘못된 답이 결과로 살아남지 못하는지 검증한다.

## 평가 계층

| Layer | 주요 평가 | 판정 방식 |
| --- | --- | --- |
| Structured Retrieval | field accuracy, scope, freshness, duplicate handling | exact assertion |
| Document RAG | recall, rerank, anchor correctness, faithfulness | gold annotation + metric |
| RAG Engine runtime | 서울 corpus 생성·import·retrieval·metadata filter·rerank, project 격리 | GCP read-back + conformance fixture |
| Extraction | 숫자·단위·표 연계 | exact or tolerance |
| Proposal Agent | schema, eligibility, unsupported Claim | deterministic validator |
| Finance | 합계·손익분기·민감도 | exact unit test |
| Typed Candidate Auditor | 누락 비용·Hard violation recall | labeled fixture |
| Runtime protocol | dispatcher, session 수명주기, full-head echo, duplicate, repair, late result | contract fixture |
| Durable Workflow | outbox, lease, redelivery, idempotency, cancel | fault-injection integration |
| MCP boundary | revision header, 10-tool Schema registry, production capability, project scope, partial status | conformance fixture |
| Guardrail | leakage·unsafe action·hallucination | forbidden behavior assertion |
| End to end | 결과 완료·근거 coverage·다음 행동 | task rubric |

## 평가 우선순위

1. 금지 행동 0건
2. cross-project leakage 0건
3. 중요한 ungrounded Claim 0건
4. 재무 계산 exact pass
5. 가맹 불가능 브랜드 추천 0건
6. 자료 부족과 Hard violation 구분
7. 문서 delta가 올바른 계산·판단 delta로 이어짐
8. 사용자에게 유용한 다음 확인 행동 제공

## Dataset 구성

### Structured fixtures

- 행정동·법정동 이름 중복
- 업소 중복·폐업잔존·업종 오분류
- 기준일과 조회일 차이
- unit·통화·기간 차이
- 일부 connector 실패

### Document fixtures

- 정보공개서 표
- 가맹계약서 조항
- 견적서 포함·제외 항목
- 대출 금리·기간·상환 조건
- 상충하는 revision
- 문서 안 Prompt Injection
- 같은 query이지만 venture project가 다른 corpus 두 개
- 오래된 revision과 최신 revision이 함께 있는 corpus
- metadata filter가 없으면 잘못된 source family가 상위에 오는 질의

실제 문서 성능을 주장하려면 사용권한이 확인된 development·calibration·sealed 자료를 source family 기준으로 분리한다. 같은 본사·template·revision 변형을 다른 split에 넣지 않는다.

### User task fixtures

- 개인카페만
- 프랜차이즈만
- 둘 다 비교
- 매물 없음
- 매물 있음
- 자료가 충분한 후보
- 자료가 부족한 후보
- 명백히 자금 범위를 넘는 후보

## Metric

### Retrieval

- Recall@k
- nDCG 또는 rerank pair accuracy
- correct source family rate
- page/table/API row anchor accuracy
- freshness classification accuracy
- geographic scope match

### Generation

- important Claim evidence coverage
- unsupported Claim rate
- abstention correctness
- missing field completeness
- forbidden assertion rate
- Proposal support validation rate
- conditional rank basis accuracy

### Extraction

- field exact match
- numeric exact match
- unit accuracy
- table header association
- document revision identity
- extraction form auto-fill exactness
- user edit preservation
- batch apply atomicity

### Finance

- line item sum exact match
- range monotonicity: low <= base <= high
- UNKNOWN zero-imputation count
- break-even formula exact match
- counterfactual threshold accuracy

### Guardrail

- cross-project retrieval count
- unsafe external action count
- franchise eligibility violation count
- benchmark-to-revenue misuse count
- Prompt Injection tool-policy change count

## LLM Judge 사용 경계

LLM Judge는 다음에 보조적으로 사용할 수 있다.

- 설명의 이해 가능성
- 다음 행동의 구체성
- 근거와 결론의 의미 일치
- 비교 카드의 중복·유용성

다음은 LLM Judge로 최종 판정하지 않는다.

- 돈 계산
- source anchor 존재
- user·project 격리
- 개인 가맹 가능 여부
- 금지된 action 실행
- schema validity

## Pass Gate

### Core

- 모든 JSON Schema가 Python `jsonschema` draft 2020-12와 Ajv 8 strict draft 2020-12의 date/date-time format에서 valid이고 공통 fixture 판정이 일치
- Finance exact test 100%
- cross-project leakage 0
- unsafe action 0
- 평균매출을 신규 점포 예상매출로 사용 0
- 가맹 미확인 브랜드 추천 순위 포함 0
- 중요한 Claim Evidence coverage 100%

### RAG

수치는 corpus와 gold set이 준비된 뒤 고정한다. 기준을 정하기 전에는 RAG가 구현됐다는 사실만으로 완료 처리하지 않는다.

### End to end

- 온보딩 네 묶음으로 결과 또는 명시적 부족 상태에 도달
- 적격 후보가 부족하면 수를 억지로 채우지 않음
- 자료 일부 누락은 조건부 결과로 설명
- 조건부 후보는 rank와 `조건부 검토`, 누락 영향이 함께 표시됨
- 결과 피드백은 확인 뒤에만 적용
- OCR 추출값은 수정 가능한 단일 폼으로 표시되고 한 번의 일괄 반영 전에는 계산에 사용되지 않음
- 문서 delta가 관련 결과만 변경

### Runtime contract

- Runtime이 검증된 `AgentTask`에서 결합한 `AgentTaskResult`의 task·invocation·venture project·full head·digest가 일치하며 모델 출력에는 이 field가 없음
- 일곱 task type이 정확한 child Agent 하나로만 dispatch되고 다른 author·복수 final·function part는 거절
- 관리형 session create→run→delete가 배포 service account로 통과하고 cleanup 실패가 durable retry됨
- repair 호출이 새 session에서도 이전 response·digest·validator error를 받음
- 같은 task의 중복 결과 중 첫 valid result만 수용
- full head 여덟 차원을 각각 바꾼 stale matrix와 취소·timeout 뒤 결과의 State write 0
- `202` 직후 API instance 종료와 Pub/Sub redelivery 뒤에도 run 유실 0, stage 중복 side effect 0
- Agent Runtime의 direct MCP call 0
- MCP `2026-07-28` JSON·SSE, method별 header, pagination 완료 production tools/list 3개와 전체 registry 10개 input/output Schema 검증
- project scope token 불일치 retrieval result 0
- 서울 Runtime·승인 생성 model·embedding·reranker의 독립 read-back; 하나라도 실패하면 Agent와 global 호출 0

## Regression

- 발견한 실패는 [고가치 평가 사례](./high-value-cases.yaml)에 추가한다.
- schema·policy·prompt·model·connector version을 결과와 함께 기록한다.
- 이전 sealed failure가 재발하면 배포를 막는다.
- 합성 fixture 통과를 실제 문서·사용자 성능으로 표현하지 않는다.

## 실행 순서

1. JSON·YAML 구문 검사
2. deterministic unit·property test
3. connector replay test
4. retrieval·extraction offline eval
5. Agent workflow eval
6. end-to-end fixture
7. sealed regression
8. 제한된 실제 사용자 task study
