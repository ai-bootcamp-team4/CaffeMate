# 데이터와 그라운딩

> 상태: draft
> 갱신일: 2026-08-23

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

### 서버 수집과 실시간 조회 경계

갱신 가능한 공공 자료를 사용자 요청 시마다 검색하지 않는다. 서버가 공급자 갱신주기에 맞춰
주기적으로 수집하고 아래 순서로 승인된 snapshot을 만든다.

```text
공식 API·공식 배포 파일
→ Cloud Storage 불변 raw와 manifest
→ schema·행 수·기간·지역코드 품질검사
→ BigQuery versioned normalized snapshot
→ read-only MCP SQL 조회
→ Claim-scoped EvidenceRecord 후보
```

- 서울 상권 자료의 첫 수집기는 Cloud Run Job `caffemate-grounding-ingest`이며 주간 Scheduler와
  배포 전 수동 실행을 사용한다. 분기 자료를 매 요청 때 다시 받지 않는다.
- 같은 원천·기간·내용은 digest 기반 `ingestion_id`와 BigQuery job id로 멱등 적재한다. 원본과
  manifest는 덮어쓰지 않는다.
- BigQuery에는 사용자 State나 모델 출력을 넣지 않는다. 정형 공공 관측과 수집 품질 metadata만
  저장한다.
- 문서 원문은 Cloud Storage에 revision별로 보관하고 Vertex AI RAG Engine에 적재한다. 정형 수치는
  BigQuery·SQL을 권위 경로로 사용하며 RAG 문맥에서 숫자를 재구성하지 않는다.
- 실시간 검색은 현재 가맹 모집·후보 지역 출점 가능 여부·최신 매물처럼 저장 snapshot이 빠르게
  낡는 항목에만 사용한다. 검색 실패가 승인 snapshot을 조용히 대체하지 않으며, 검색 결과도
  원문·조회 시각·scope를 검증하기 전에는 임시 Evidence 후보다.
- 공급자 SLA를 넘은 snapshot은 `STALE`로 표시한다. 최신 조회 실패 시 과거 값을 `FRESH`로
  재표시하거나 0으로 바꾸지 않는다.
- 서울 전용 수요·추정매출 자료가 없는 지역에는 같은 품질의 전국 수치를 생성하지 않고
  `UNKNOWN`과 coverage 차이를 유지한다.

### Structured Retrieval

다음은 API·SQL·공간 질의로 가져온다.

- 인구·연령
- 업소·인허가 event
- 브랜드 비용 필드
- 지리 경계·point-in-polygon
- freshness와 coverage

벡터 검색 결과를 정형 수치의 최종값으로 사용하지 않는다.

첫 서울 운영 connector는 `get_area_profile`과 `search_cafe_observations`다. 선택된 10자리
법정동 코드를 최신 승인 `area_mapping`으로 하나 이상의 8자리 행정동에 연결한 뒤 같은
`ingestion_id`의 사실만 합산한다.

- 카페 수·신규·폐업 수는 서울시 `커피-음료` 분기 행정동 집계다.
- `CLOSURE_RATE`는 여러 행정동의 폐업 수를 현재 점포 수로 나눈 결정론적 파생값이며
  `DERIVED_RESULT`와 계산 방법을 표시한다. 서울시의 개별 점포 생존확률이 아니다.
- `ESTIMATED_SALES`는 서울시 행정동 카페 업종의 분기 추정매출 합계다. 실제 카드매출이나 신규
  점포 예상매출로 표시하지 않는다.
- 유동인구는 분기 집계 방문량 추정치이며 주민인구·직장인구와 별도 단위로 유지한다.
- 소비·생존율처럼 아직 연결되지 않은 지표는 `PARTIAL`로 남기며 0 또는 유사 지표로 대체하지
  않는다.
- 서울 외 지역 또는 공식 법정동→행정동 매핑은 존재하지만 서울 fact가 없는 지역은
  `NOT_FOUND`/`UNKNOWN`으로 남긴다.
- 구조화 수치 EvidenceRecord는 Claim 종류와 별도로 원 공급자의 `metric` 식별자를 보존한다.
  결과 API는 후보의 동결 근거에 실제로 포함된 카페 수·신규·폐업·폐업 변화율·상권 추정매출만
  `market_signals`로 투영하며 값, 기준일, 신선도, 공식 원문과 지표별 주의 문구를 함께 반환한다.
  결과 화면은 내부 Claim 코드나 Evidence id 대신 이 사용자용 투영값을 표시한다.

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

- Vertex AI RAG Engine을 공식 문서·정보공개서·사용자 문서의 주 검색 계층으로 사용한다.
- 원본 revision은 Cloud Storage에 불변 보관하고, RAG corpus·file id와 document revision·checksum·project scope·원문 anchor의 대응 관계는 Cloud SQL에 기록한다. Cloud SQL은 제품 State와 Evidence ledger의 정본이지 주 vector serving 계층이 아니다.
- 공식 corpus와 사용자 project corpus를 물리적으로 분리한다. 첫 구현에서 사용자 문서는 venture project별 허용 corpus 또는 명시적인 허용 file id 밖으로 검색할 수 없다.
- RAG Engine의 Document AI Layout Parser 연동으로 제목·조항·표·목록 구조를 보존한 chunk를 생성한다. import 결과와 실패 목록은 Worker가 기록하고 불완전 generation을 검색에 사용하지 않는다.
- 검색은 Claim 분해, corpus routing, source·문서 revision·기준일 metadata filter, semantic retrieval, Vertex AI Ranking API rerank, 원문 anchor 복구, entailment·단위·scope 검증과 반대 근거 검색 순서로 수행한다.
- MCP의 RAG connector는 공급자 중립 검색 hit와 `source_trace`를 반환한다. Control API의 `EVIDENCE_RETRIEVAL` adapter가 현재 Claim type·지리 범위·기준일과 결합해 schema-valid `EvidenceRecord` 후보를 만든다. 출처 trace와 연결되지 않는 hit는 후보로 만들지 않고 `PARTIAL`로 남긴다.
- RAG hit는 검색 성공일 뿐 확정 근거가 아니다. `EVIDENCE_ASSESS`가 관계·범위·날짜·신선도·anchor·권위를 수용한 후보만 Evidence Freeze에 들어간다.
- 계약번호·사업자번호·브랜드 id·금액·날짜 같은 exact field는 Cloud SQL의 typed lookup을 병렬 사용한다. 이 결과와 RAG context는 같은 `EvidenceRecord` 검증을 통과해야 한다.
- RAG Engine이 제공하는 hybrid search는 선택한 vector backend와 서울 리전에서 실제 지원되는 경우에만 사용한다. 지원되지 않으면 semantic retrieval과 exact lookup을 결합하며 기능을 허위 표기하지 않는다.
- `asia-northeast3`의 Preview 위험은 승인된 구현 제약이다. corpus 생성, Layout Parser import, retrieval, metadata filter와 rerank read-back을 배포 Gate로 둔다.
- RAG Engine 장애를 Cloud SQL `pgvector`, `global` endpoint 또는 다른 리전으로 조용히 우회하지 않는다. 필수 경로 실패는 `RAG_UNAVAILABLE` 또는 `BLOCKED_BY_REGION`으로 노출한다.
- 동일한 sealed retrieval set으로 Recall@k, nDCG 또는 pair accuracy, anchor accuracy, project 격리, latency와 비용을 측정한다.

공식 기능 근거: [RAG Engine 지원 리전](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/rag-overview), [RAG Engine API](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/rag-api), [Layout Parser 연동](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/layout-parser-integration), [metadata search](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/use-metadata-search), [reranking](https://cloud.google.com/vertex-ai/generative-ai/docs/retrieval-and-ranking). `accessed_at: 2026-08-21`, `freshness: deployment preflight에서 재확인`이다.

## Advanced RAG Pipeline

```text
deterministic Claim type routing
→ versioned support·counter query template
→ authority·region·document type·data date filter
→ RAG semantic retrieval + exact typed lookup
→ result fusion
→ rerank
→ page·table·API row anchor recovery
→ Claim-scoped EvidenceRecord candidate adapter
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
