# 데이터와 그라운딩

> 상태: draft
> 갱신일: 2026-08-21

## 핵심 원칙

결과 카드의 중요한 문장은 아래 다섯 종류 중 하나여야 한다.

| Kind | 의미 |
| --- | --- |
| `EVIDENCED_FACT` | 외부 원문·API에 연결된 사실 |
| `USER_CONFIRMED_FACT` | 사용자가 확인한 조건 |
| `DECLARED_ASSUMPTION` | 출처·적용 범위를 표시한 임시 가정 |
| `DERIVED_RESULT` | 위 입력으로 재현 가능한 계산 |
| `UNKNOWN` | 현재 확인할 수 없는 값 |

LLM 문장 자체는 Evidence가 아니다.

## Evidence 우선순위

1. 정부·공공기관·법령·공식 문서
2. 기업 공식 공개자료와 정보공개서
3. 원 데이터와 원 논문
4. 검증된 업종 참고 자료
5. 사용자 제공 문서
6. 기타 2차 자료

사용자 문서는 해당 후보의 실제 조건에는 강하지만 일반 법률·시장 사실을 확정하는 근거가 되지는 않는다.

## Evidence 최소 metadata

기계 계약은 [Evidence Record Schema](../contracts/evidence-record.schema.json)를 따른다.

```yaml
evidence_id: required
project_id: required
claim_type: required
value: required
geographic_scope: required
source_title: required
source_uri: required
source_authority: required
published_or_data_date: required
retrieved_at: required
original_anchor: required
freshness_status: required
conflict_status: required
```

## 필요한 데이터

### P0 — 첫 제안

| Data | 최소 범위 | 사용 |
| --- | --- | --- |
| 행정동 identity·경계 | 전국 | 지역 해석과 공간 join |
| 주민 인구·연령 | 행정동·기준일 | 거주 수요 환경 |
| 카페 업소 관측 | 업소 row·좌표·업종·기준일 | 경쟁 공급 환경 |
| 신규·폐업 | 가능 지역·기간·인허가 정의 | 변화 신호 |
| 개인카페 표준 모델 | 면적·장비·인력·비용 range | 개인카페 후보 |
| 프랜차이즈 | 가맹 가능·비용·등록·기준연도 | 실제 브랜드 후보 |
| 임대료 참고값 | 표본 상권·층·포함 항목 | 필요한 매물 조건 |
| 공식 준비 절차 | 지역·절차·기준일 | 교육·신고·등록 안내 |

### P1 — 후보 선택 이후

- 실제 매물 주소·면적·층·보증금·월세·관리비·권리금·시설
- 최신 정보공개서·가맹계약서·본사 상담 조건
- 장비·인테리어 견적과 포함·제외 항목
- 대출 금리·기간·상환 조건
- 관할 기관 확인 결과

### P2 — 보강 자료

- 실제 또는 공급자 추정매출
- 유동인구·직장인구·소비
- 특정 점포 임대 시세와 권리금
- 카페 생존율·프랜차이즈 폐점률

P2는 전국 공통 필수로 약속하지 않는다. 없으면 예상매출을 생성하지 않고 손익분기와 필요한 주문 수로 대체한다.

## Coverage Profile

같은 UI 계약을 유지하되 지역별 자료 가능성을 profile로 명시한다.

```text
N0_NATIONWIDE_FACTS
→ N1_NATIONWIDE_CONDITIONAL
→ R2_REGIONAL_CONNECTOR
→ C3_CASE_ARTIFACT
```

| Level | 의미 | 허용 출력 |
| --- | --- | --- |
| N0 | 전국 공통 read-back 자료 | 행정동, 인구·연령, raw 카페 관측 |
| N1 | key·품질 검증 후 전국 자료 | 개폐업 event, 사업체·종사자 등 |
| R2 | 지역 공급자 자료 | 공급자 방법을 유지한 매출·유동 prior |
| C3 | 사용자 후보 실제 자료 | 매물·비용·계약 조건 |

다른 profile 값을 같은 기준으로 합쳐 전국 자동 순위를 만들지 않는다.

## Structured Retrieval과 RAG

### Structured Retrieval

다음은 API·SQL·공간 질의로 가져온다.

- 인구·연령
- 업소·인허가 event
- 브랜드 비용 필드
- 지리 경계·point-in-polygon
- freshness와 coverage

벡터 검색 결과를 정형 수치의 최종값으로 사용하지 않는다.

### Official Document RAG

- 정보공개서
- 인허가·시설·교육 공식 안내
- 법령과 표준 문서

### Project Document RAG

- 매물 자료
- 견적서
- 계약서
- 대출 조건

### CONFIRMED — 첫 운영 RAG backend

- 원문과 parsing 산출물은 Cloud Storage에, 문서 revision·chunk·anchor·project scope metadata는 Cloud SQL에 저장한다.
- 첫 운영 hybrid retrieval은 PostgreSQL full-text search와 pgvector를 사용한다.
- retrieval interface는 backend와 분리해 동일한 Claim query와 `EvidenceRecord` 계약을 유지한다.
- 서울 리전에서 Preview인 RAG Engine은 운영 필수 경로에 두지 않는다. 필요하면 같은 interface 뒤의 실험 adapter로만 연결한다.
- corpus 규모나 평가 결과가 Cloud SQL 기준을 넘으면 Vertex AI Vector Search를 후보로 비교한다. 서비스 이름이나 기능 선호만으로 미리 이전하지 않는다.
- backend 변경 전후에 동일한 sealed retrieval set으로 Recall@k, anchor accuracy, project 격리, latency와 비용을 비교한다.
- embedding model은 `asia-northeast3` 지원 여부를 배포 시점에 확인하고, 미지원이면 `BLOCKED_BY_REGION`으로 중단한다. `global` 또는 다른 리전으로 전환하지 않는다.

## Advanced RAG Pipeline

```text
Claim query decomposition
→ authority·region·document type·data date filter
→ keyword and vector retrieval
→ reciprocal rank fusion
→ rerank
→ page·table·API row anchor recovery
→ entailment·unit·scope validation
→ counterevidence search
→ EvidenceRecord or ABSTAIN
```

### Chunk 규칙

- 제목·조항·표·페이지 경계를 보존한다.
- 표 row는 헤더·단위·기준연도와 함께 저장한다.
- 모든 chunk는 source version과 checksum을 가진다.
- official corpus와 project corpus를 논리적으로 분리한다.
- project filter 없는 사용자 문서 검색은 실행하지 않는다.
- money·date·rate는 원문 anchor에서 다시 확인한다.

## 참고 비용 범위

실제 견적 전 임시 가정으로만 사용한다.

- 출처, 기준연도, 표본 또는 적용 대상
- low·base·high range
- 포함·제외 항목
- 지역·면적·운영 모델 적용 범위

허용:

- 초기비용
- 인력
- 면적
- 장비
- 운영비 line item

금지:

- 예상매출
- 예상 고객 수
- 상권 수요
- 창업 성공확률

## Franchise Grounding

- 브랜드 실재와 개인 가맹 가능 여부는 순위 전 확인한다.
- 정보공개서·본사 자료 revision을 기록한다.
- 일부 필드가 없어도 조건부 후보가 될 수 있다.
- 누락 필드를 다른 브랜드 값으로 채우지 않는다.
- 평균매출은 신규 점포 예상매출이 아니다.
- 출점 가능·영업지역 보호는 본사 확인 전 `UNKNOWN`이다.

## Conflict

같은 Claim에 다른 값이 있으면 자동 winner를 고르지 않는다.

```yaml
conflict_id: required
claim_type: required
candidate_evidence_ids: []
materiality: LOW | MEDIUM | HIGH
resolution: UNRESOLVED | USER_CONFIRMED | SOURCE_SUPERSEDED
```

HIGH conflict는 계산·추천에 미치는 영향을 표시하고 필요한 경우 결과를 `검토 필요`로 낮춘다.

## Freshness

- 공급자별 갱신주기와 허용 age를 설정한다.
- `published_or_data_date`와 `retrieved_at`을 분리한다.
- 최신 조회 성공이 오래된 data period를 최신 자료로 만들지 않는다.
- connector 실패 시 이전 Evidence를 `FRESH`로 재표시하지 않는다.
- 현재성 요구가 높은 가맹 가능·출점·계약 조건은 별도 확인 action을 둔다.

## 현재 확인된 전국 공통 한계

- 주민등록 인구는 유동·직장·소비 인구가 아니다.
- 카페 업소 snapshot은 중복·폐업잔존·분류 오류가 있을 수 있다.
- 실제 매출·유동·임대·권리금·생존율은 전국 동네에서 같은 품질로 확보되지 않는다.
- 사업자번호 상태조회는 번호를 알고 있을 때만 가능하며 상호·주소로 번호를 발견하는 수단이 아니다.

## Grounding 수용 기준

- 중요한 사실의 Evidence coverage가 100%다.
- 모든 숫자는 unit·scope·data date를 가진다.
- 원문 anchor가 실제 source revision에서 재현된다.
- UNKNOWN을 0으로 계산하지 않는다.
- conflict가 output에서 숨겨지지 않는다.
- project corpus 검색에서 다른 프로젝트 결과가 0건이다.
