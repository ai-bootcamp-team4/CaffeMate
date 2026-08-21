# 카페 창업안 의사결정 모델

> 상태: draft
> 갱신일: 2026-08-21

## 목적

후보를 예상매출 하나로 정렬하지 않고 창업자의 제약, 경제성, 운영 적합성과 위험을 순서대로 평가한다.

```text
Hard Constraint
→ Economic Viability
→ Founder Fit
→ Risk-adjusted Ranking
```

## 1. Candidate Eligibility

### 공통

- 선택 지역과 연결 가능
- 후보 유형과 사용자 선택이 일치
- 최소 비용 범위를 만들 근거 또는 표시된 가정이 존재
- 존재하지 않는 비용·매출을 생성하지 않음

### 프랜차이즈

- 실제 브랜드 확인
- 개인 가맹 가능 여부 확인 필수
- 공식 정보공개서 또는 본사 공개자료 연결
- 특정 동네 출점 가능 여부는 확인 전 `본사 확인 필요`

정보공개서·비용·계약 자료 일부가 없어도 후보가 될 수 있다. 누락을 다른 브랜드 값으로 채우지 않고 `조건부 검토`로 둔다.

### 개인카페

- 등록된 표준 운영 모델을 기반으로 생성
- 조정된 변수와 원래 모델을 추적
- 모델 근거 밖 값은 `UNKNOWN` 또는 추가 검토

## 2. Hard Constraint

Hard Constraint는 확인된 사실 또는 현실적 전체 범위에서 성립해야 한다.

예:

- 개인 가맹 불가능
- 사용자 필수 유형과 불일치
- 전체 비용 범위가 가용 가능한 자금 범위를 명백히 초과
- 필수 시설·운영 조건을 충족할 수 없음이 확인됨

자료가 없다는 사실만으로 Hard Constraint 위반으로 판정하지 않는다.

## 3. Economic Viability

### 입력

- 사용자 자기자금
- 대출 고려 상태와 확정된 대출 조건
- 초기 고정 투자
- 보증금·권리금·임차 관련 현금
- 개업 전 비용
- 예비운영비
- 월 고정비
- 원가율·객단가·주문 수 사용자 가정

### 계산

```text
total_initial_cash
= deposits
+ acquisition_or_premium
+ construction
+ equipment
+ franchise_initial_fees
+ preopening_costs
+ opening_inventory
+ contingency
+ operating_reserve
```

```text
monthly_break_even_sales
= monthly_fixed_costs / contribution_margin_rate
```

```text
required_daily_orders
= monthly_break_even_sales / operating_days / assumed_average_ticket
```

실제 매출 자료가 없으면 `required_daily_orders`를 보여줄 수 있지만 예상 주문 수로 표현하지 않는다.

### Scenario

- `LOW`: 비용 범위의 낮은 값
- `BASE`: 범위의 대표값
- `HIGH`: 비용 범위의 높은 값

각 line item은 사실·사용자 입력·참고 비용 범위·UNKNOWN 중 하나를 가진다. UNKNOWN을 0으로 계산하지 않는다.

## 4. Founder Fit

점수 하나보다 조건별 상태를 사용한다.

- 직접 운영 시간과 후보 운영 부담
- 직원 의존도
- 메뉴·공정 복잡도
- 브랜드 통제 수용 여부
- 창업자의 경험과 교육 필요
- 자금 조달 의향
- 선호·회피 조건

`미정`은 낮은 점수가 아니라 확인할 항목이다.

## 5. Risk

- 비용 누락
- 자료 freshness
- Evidence 충돌
- 특정 동네 출점 미확인
- 필수품목·로열티·광고·시스템비 누락
- 임대료·관리비·권리금 미확인
- 실제 매출·유동 자료 없음
- Founder 운영부담 과소평가
- 문서 추출 불확실성

Risk는 Evidence 또는 명시된 missing field에 연결한다.

## 6. 사용자 표시 상태

| 상태 | 판정 |
| --- | --- |
| `검토 추천` | 필수 조건을 충족하고 현재 근거에서 다음 조사 가치가 있음 |
| `조건부 검토` | 자료가 부족하거나 특정 조건에서만 성립 |
| `제외` | 확인된 사실과 현실적 전체 범위에서 필수 조건을 충족할 수 없음 |

`검토 추천`은 창업 실행 권고가 아니다.

## 7. Ranking

1. Hard Constraint 위반 후보 제거
2. 최소 자금 적합 범위 확인
3. Founder Fit의 명백한 충돌 확인
4. 현재 Evidence coverage와 risk 비교
5. 지배당하지 않는 후보를 우선
6. `다음으로 조사할 가치` 기준으로 주력 후보 선택

불완전한 후보가 주력일 수 있다. 이 경우 `가장 우수한 확정안`이 아니라 `불확실성을 줄일 가치가 가장 높은 후보`라고 표시한다.

### 순위 의미

| 상태 | rank 허용 | rank 의미 |
| --- | --- | --- |
| `검토 추천` | 허용 | 현재 확인된 경제성·Founder Fit·위험 비교 |
| `조건부 검토` | 허용 | 현재 근거에서 다음으로 검토할 우선순위 |
| `제외` | 금지 | 없음 |

조건부 후보는 `2순위 — 조건부 검토`처럼 표시할 수 있다. 카드에는 누락값, 그 값이 판단에 미치는 영향과 다음 확인 행동을 rank와 같은 시야에 표시한다. 조건부 rank를 확정 수익성·성공 가능성 순위로 설명하지 않는다.

## 8. Counterfactual

모든 주력 후보에는 판단 반전 조건을 제공한다.

예:

- 월 점유비가 얼마 이하가 되면 자금 Gate를 통과하는가
- 초기 투자비가 얼마나 낮아져야 개인카페가 프랜차이즈보다 유리한가
- 필수품목 원가가 어느 범위를 넘으면 후보가 제외되는가
- 직접 운영 시간이 부족하면 어떤 운영 모델로 바뀌어야 하는가

Counterfactual은 deterministic calculation으로 생성한다.

## 9. 금지 규칙

- 브랜드 평균매출을 신규 점포 예상매출로 변환하지 않는다.
- 주민 인구를 유동인구로 변환하지 않는다.
- 참고 비용 범위로 고객 수·매출·성공확률을 만들지 않는다.
- 누락값을 0 또는 다른 후보 평균으로 대입하지 않는다.
- 법적 안전과 계약 가능성을 확정하지 않는다.
- Agent의 자연어 설명을 계산값으로 사용하지 않는다.

## 10. 결과 계약

결과는 [Candidate Result Schema](../contracts/candidate-result.schema.json)를 따른다.

필수 출력:

- 현재 상태와 의미
- 추천 근거
- 비용 range와 line item provenance
- Evidence와 기준일
- UNKNOWN·STALE·충돌
- 위험과 missing field
- 판단 반전 조건
- 다음 확인 행동
