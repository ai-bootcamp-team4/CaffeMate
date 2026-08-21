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
| Extraction | 숫자·단위·표 연계 | exact or tolerance |
| Proposal Agent | schema, eligibility, unsupported Claim | deterministic validator |
| Finance | 합계·손익분기·민감도 | exact unit test |
| Critic | 누락 비용·Hard violation recall | labeled fixture |
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

### Extraction

- field exact match
- numeric exact match
- unit accuracy
- table header association
- document revision identity

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

- 모든 JSON Schema valid
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
- 결과 피드백은 확인 뒤에만 적용
- 문서 delta가 관련 결과만 변경

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
