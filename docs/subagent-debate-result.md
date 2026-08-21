# CaffeMate 독립 감사 통합 결과

> 상태: active
>
> 정본: [제품 명세](./product-spec.md)
>
> 갱신일: 2026-08-21

레포의 제품 설명은 충분했습니다. 부족했던 건 제품 범위가 아니라, 이를 실제로 구현 가능한 계약—상태 전이, 데이터 권리, 후보 판정, RAG 승격, Agent 권한, 문서 생명주기—으로 닫는 부분이었습니다.

최종안은 “MVP”가 아니라 로그인부터 계약 전 판단자료까지의 전체 제품입니다. 구현 순서만 단계화하며 기능 범위를 줄이지 않습니다.

## 1. 제품이 끝까지 수행하는 일

```text
로그인·개인 창업 프로젝트
→ 온보딩
→ 지역 identity와 데이터 coverage 확정
→ 조사할 Claim 목록 생성
→ 개인카페 모델·프랜차이즈 리드 조사
→ 근거 검증
→ 재무·창업자 적합성·필수조건 판정
→ 후보 비교
→ 자연어 피드백의 변경안 확인
→ 조사 대상 후보 선택
→ 매물·견적·계약·대출·시설 문서 업로드
→ Claim 추출·자동 입력 폼·일괄 반영·충돌 해결
→ 영향받은 항목만 재계산
→ 계약 전 판단자료 생성
→ 출처·문서 갱신에 따른 stale 처리와 재계산
```

후보는 최대 3개지만 억지로 채우지 않습니다. 제품의 결과는 “창업 추천”이 아니라 “현재 근거에서 다음 조사 비용을 투입할 대상과 확인할 사항”입니다.

## 2. 후보 상태와 판단 규칙

내부 판정과 사용자에게 보여주는 차선을 분리합니다.

| 차선 | 의미 | 순위 | 선택 |
|---|---|---:|---|
| `LEAD_ONLY` | 브랜드나 업체만 식별됐고 개인 가맹 가능 여부가 미확인 | 없음 | 불가 |
| `REVIEW_RECOMMENDED` | 현재 필수 조건과 비교 근거가 확인됨 | 경제성·Founder Fit 비교 rank | 조사 대상으로 가능 |
| `CONDITIONAL_REVIEW` | 개인 가맹 가능은 확인됐지만 비용·계약·출점 자료 일부가 미확인·오래됨·충돌함 | 다음 검토 우선순위 rank | 조사 대상으로 가능 |
| `EXCLUDED` | 확인된 hard constraint 위반 | 없음 | 불가 |

내부 Gate는 `PASS | FAIL | UNRESOLVED`로 고정합니다.

- 확인된 Hard FAIL만 `EXCLUDED`
- 중요 `UNKNOWN`, `STALE`, `CONFLICT`가 있어도 개인 가맹 가능과 최소 후보 identity가 확인되면 `CONDITIONAL_REVIEW`로 결과에 포함할 수 있음
- 자료 부족을 실패나 0으로 해석하지 않음
- 개인 가맹 가능 여부가 확인되지 않은 프랜차이즈는 `LEAD_ONLY`이며 결과 rank에 포함하지 않음
- 특정 지역·점포 출점 가능성과 실제 총비용이 미확인이면 `CONDITIONAL_REVIEW`이며 카드에 누락과 영향을 표시
- 조건부 후보는 `2순위 — 조건부 검토`처럼 표시할 수 있지만 확정 경제성 순위가 아니라 다음 검토 우선순위임

공정위 평균매출은 역사적 가맹점 통계일 뿐 신규 점포 예상매출로 사용하지 않습니다. [공정위 정보공개서 목록](https://www.data.go.kr/data/15125569/openapi.do), [브랜드 평균매출 API](https://www.data.go.kr/data/15125494/openapi.do)

종합 가중점수는 폐기합니다. 동일한 snapshot·범위·단위·기준일을 가진 확인된 축끼리만 Pareto 비교합니다. 비지배 후보가 하나일 때만 “다음 검토 우선 후보”로 표시하고, 동률이나 비교 불가능이면 공동 검토 대상으로 둡니다.

## 3. 데이터 원천

| 영역 | 주 원천 | 제품 내 용도 |
|---|---|---|
| 지역·주소·경계 | [주소 API](https://www.data.go.kr/data/15057017/openapi.do), [SGIS](https://sgis.kostat.go.kr/developer/) | 행정동·법정동 identity, 경계 버전, 공간 결합 |
| 거주 인구 | [행안부 주민등록 통계](https://jumin.mois.go.kr/), [KOSIS](https://kosis.kr/openapi/index/index.jsp) | 월별 주민등록 인구·연령. 유동인구나 수요로 해석하지 않음 |
| 사업체·카페 관측 | [행안부 일반음식점](https://www.data.go.kr/data/15045016/fileData.do), [소진공 상가정보](https://www.data.go.kr/data/15012005/openapi.do) | 등록 업소 관측, 업종 분포. 실제 영업·매출·좌석 수로 해석하지 않음 |
| 지역별 소비·유동 | [소상공인365](https://www.data.go.kr/data/15143517/fileData.do), 지역 공공데이터 | 제공 지역과 조사 방법이 확인된 경우에만 별도 지표로 사용 |
| 부동산 비교치 | [국토부 상업업무용 실거래](https://www.data.go.kr/data/15126463/openapi.do), [한국부동산원 임대동향](https://www.reb.or.kr/reb/cm/cntnts/cntntsView.do?cntntsId=1049&mi=10335&statId=S237220284) | 역사적 거래·표본 임대 benchmark. 현재 매물 조건으로 사용하지 않음 |
| 프랜차이즈 | [정보공개서 본문](https://www.data.go.kr/data/15125571/openapi.do), [부담금·인테리어](https://www.data.go.kr/data/15143711/openapi.do), 본사 공식 자료 | 브랜드 identity, 공개 비용과 역사적 현황 |
| 사업자·법인 | [국세청 상태조회](https://www.data.go.kr/data/15081808/openapi.do), [OpenDART](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001) | 알려진 사업자번호·법인 identity 확인 |
| 법·절차 | [국가법령정보센터](https://www.law.go.kr/), [정부24 영업신고](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=14600000021&HighCtgCD=A09006&tp_seq=08), [식품안전나라](https://www.foodsafetykorea.go.kr/) | 관할·절차·필요서류·근거 조항 |
| 정책자금 | [소상공인 정책자금](https://ols.semas.or.kr/ols/pfa/SPFA207P/page.do), [기업마당](https://www.bizinfo.go.kr/), [신용보증재단중앙회](https://www.koreg.or.kr/) | 상품·신청조건·유효기간. 승인이나 한도를 예측하지 않음 |
| 실제 후보 조건 | 사용자 업로드·직접 입력·허가된 공급자 feed | 매물, 임대료, 권리금, 견적, 계약, 대출 조건의 핵심 원천 |

사용하지 않는 원천도 확정했습니다.

- 네이버·직방·다방 등의 비공개 API, 로그인·CAPTCHA·robots 우회
- 카드사·통신사·은행의 비공개 원시 데이터
- 본사·중개사·시공업체의 비공개 자료를 허가 없이 수집
- 공개 URL이라는 이유만으로 전문을 저장·임베딩·재배포
- 다른 기업의 CRM·ERP·메일·메신저 데이터

각 원천은 `READ`, `STORE`, `CACHE`, `TRANSFORM`, `EMBED`, `EXCERPT`, `REDISTRIBUTE`, `TRAIN` 권한을 별도로 관리합니다. 이용조건이 불명확하면 `BLOCKED_BY_TERMS`입니다. 공개데이터도 무제한 재배포 권리를 의미하지 않습니다. [공공데이터법 제17조](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1020989551)

## 4. RAG 설계

구조화 수치와 공간 정보는 RAG에 넣어 답을 생성하지 않습니다.

- SQL/PostGIS: 지역코드, 경계, 인구, 사업체, 비용원장, 날짜, freshness, 공간 결합
- RAG: 법령·절차·정보공개서·본사 문서·사용자 계약서와 견적서의 조항, 표, 예외, 문맥 탐색
- LLM/vector 검색 결과 자체는 Evidence가 아님
- 최종 Evidence는 원본 revision과 anchor를 다시 검증해야 함

Corpus는 다음처럼 물리적으로 분리합니다.

1. `public-official`
2. `licensed`
3. `project-private`
4. 각 corpus의 `current`와 `historical`

프로젝트 문서 ACL은 object storage, SQL, sparse index, vector index, reranker, cache, 비동기 작업, 로그까지 전파합니다. project filter가 빠지면 검색 결과 0건이 아니라 요청 자체를 fail-closed 처리합니다.

수집 파이프라인은 다음으로 고정합니다.

```text
Source 등록·권리 확인
→ 격리 수집·checksum
→ immutable SourceRevision
→ OCR/layout/table 파싱
→ 정규화·품질 검증
→ chunk와 anchor 생성
→ sparse/dense index shadow 구축
→ sealed 평가
→ atomic index generation 전환
```

Anchor는 다음 수준까지 복원합니다.

- PDF: 페이지, 인쇄 페이지, section, 표, 행·열, header, 단위, bbox
- 웹: canonical URL, heading path, 게시·수집일
- API: snapshot, 요청 필터, pagination, row key, JSON path
- 스프레드시트: sheet, cell range, header, 단위
- 모든 경우 source checksum과 parser/model version

검색 전에 atomic Claim Plan을 만듭니다. 각 Claim은 대상, predicate, 값 형식, 단위, 지역 범위, 기준일, 권위 수준, freshness, 중요도, SQL/RAG route, 반대 근거 검색, 중단 규칙을 가집니다.

초기 검색 설정은 다음으로 시작합니다.

- sparse top 50
- dense top 50
- RRF `k=60`, sparse/dense `0.6/0.4`
- fusion 60 → rerank 30 → anchor recovery 8
- Claim당 최종 Evidence 최대 5
- 반대 근거는 sparse/dense 각각 top 20
- exact ID·금액·단위·날짜는 sparse-only
- 위 숫자는 sealed 평가로 corpus별 조정

중요 값의 anchor·scope·date·unit·revision·checksum이 하나라도 맞지 않으면 확정하지 않습니다.

## 5. 최종 Agent는 5개

기능은 전부 유지하지만 LLM Agent는 실제 의미 추론이 필요한 다섯 역할만 둡니다.

| Agent | 역할 | 절대 하지 않는 일 |
|---|---|---|
| Intent Interpreter | 결과 이후 자연어 피드백을 typed delta로 변환 | 확인 전 State 변경, 검색, 재무 계산 |
| Evidence Researcher | Claim gap, 검색계획, 근거 후보와 반대 근거 제안 | Evidence 확정, 후보 판정·순위 |
| Proposal Agent | frozen Evidence와 등록 모델·실제 브랜드 안에서 개인·프랜차이즈 후보안을 구조화 | 브랜드·비용·매출 발명, 계산·Gate·순위 |
| Document Analyst | OCR·표 결과를 계약·견적 등의 typed Claim proposal로 연결 | 문서 효력 판단, Claim 자동 확정 |
| Typed Candidate Auditor | 확정 직전 snapshot의 누락·무근거·상태 모순·숨은 충돌 탐지 | 제외·순위·State 변경 |

그 밖의 기능은 결정론적 코어입니다.

- 인증·권한·Safety Gate
- 지역 identity와 공간 분석
- Source connector와 freshness
- 개인카페 표준 모델 적용
- 프랜차이즈 catalog·eligibility
- Claim/Evidence validator
- 재무·창업자 적합성·Gate·Pareto
- 충돌과 dependency graph
- 절차 resolver
- 사용자 Claim review workflow
- 상태 reducer/CAS
- 계약 전 패킷 renderer

Agent는 모두 typed proposal만 출력하며 State, Evidence, 비용, Gate, 순위에 직접 쓰지 못합니다.

문서 분석 결과는 필드별 확인창으로 묻지 않습니다. OCR·표 추출값과 원문 anchor를 한 개의 수정 가능한 폼에 자동 입력하고, 사용자가 필요한 값을 고친 뒤 `반영하고 다시 계산`을 한 번 누르면 전체 폼을 원자 적용합니다. 애매한 값은 추측하지 않고 빈 필드와 경고로 남기며, 일괄 반영 전에는 계산이나 순위를 바꾸지 않습니다.

## 6. 재무와 창업자 적합성

비용은 정수 KRW line-item 원장으로 관리합니다.

- 보증금, 권리금, 중개비
- 임대료, 관리비
- 철거·전기·급배수·냉난방·소방·간판·인테리어
- 커피장비·냉장·제빙·세척·POS·가구
- 가맹비·교육비·로열티·광고비·필수품목
- 초도재고·개점비
- 인건비·사업주 부담금
- 공과금·보험·유지보수
- 운전자금·예비비·contingency
- 대출 원리금·세금·결제수수료

각 line에는 one-off/monthly/variable, 현금 필요액/비용, 환급 가능성, VAT, 포함·제외 범위, 면적·수량, 기준일·유효기간, 출처를 둡니다.

중요 line이 미확정이면 총액은 `null`입니다. 알려진 부분합만 “확인된 부분합”으로 표시합니다. 보증금은 현금 필요액에는 포함하지만 비용에는 포함하지 않습니다.

손익분기와 필요 주문 수는 사용자 확인 객단가·영업일·변동비가 있을 때만 계산하며 예상 고객 수나 매출 전망으로 표현하지 않습니다. `low/base/high`는 확률이 아니라 별도 가정 시나리오입니다.

창업자 적합성은 자금·손실 감내·운영시간·현장 상주·직원 의존·메뉴 복잡도·브랜드 자율성·경험·교육·생활조건을 typed dimension으로 관리합니다. 점수 합산은 하지 않고 hard/soft/open과 known/unknown/conflict를 분리합니다.

## 7. 상태·권한·동시성

모든 데이터는 immutable revision이고 `ProjectHead`만 현재 포인터입니다.

```text
State
Evidence
Policy
Candidate seed
Workflow generation
Current result
Selection
Packet
```

모든 명령은 다음을 요구합니다.

- 서버 인증에서 얻은 actor
- 인증된 사용자의 프로젝트 소유권
- idempotency key
- 기대하는 전체 head tuple
- 프로젝트와 snapshot scope

사용자 변경은 event, 새 State revision, 기존 결과 무효화, workflow 생성, outbox를 한 DB transaction으로 기록합니다. Worker와 Agent는 staging에만 기록하고 reducer가 최종 CAS를 수행합니다.

- timeout·partial·late·failed 결과는 current가 될 수 없음
- 새 입력에서 재계산이 실패해도 이전 결과를 새 current로 복원하지 않음
- 피드백 취소는 persistent write 0건
- Undo는 과거 삭제가 아니라 compensating revision
- 문서 삭제는 tombstone, 관련 Claim `RETRACTED`, 종속 결과 재계산
- Source 철회는 과거판 보존, current 무효화
- 충돌한 동시 변경은 자동 merge하지 않고 `409`

## 8. UX와 계약 전 판단자료

핵심 정보구조는 다음입니다.

```text
프로젝트
├─ 온보딩·Coverage
├─ 분석 진행과 heartbeat
├─ 후보 비교
│  ├─ 근거
│  ├─ 불확실성
│  └─ 다음 확인 행동
├─ 선택 후보
│  ├─ 문서
│  ├─ Claim 검토
│  ├─ 충돌
│  └─ 재계산 차이
├─ 판단 이력
└─ 계약 전 판단자료
```

모든 숫자에 `확인된 사실 / 사용자 사실 / 가정 / 파생 계산 / 미확인`과 출처·범위·기준일을 표시합니다. 진행률을 정확히 계산할 수 없으면 가짜 퍼센트 대신 단계·checkpoint·heartbeat를 보여줍니다.

패킷의 source of truth는 immutable JSON이고 HTML/PDF는 파생물입니다. 포함 내용은 다음입니다.

- 프로젝트·후보·모든 snapshot digest
- 확인된 사실과 사용자 입력
- 가정·UNKNOWN·STALE·CONFLICT
- 초기 현금·월 비용·손익분기
- 창업자 적합성
- 프랜차이즈·임대·견적·계약·대출·절차
- 변경 이력과 재계산 차이
- 다음 질문과 전문가 확인 목록
- 모든 출처·anchor·revision

상태는 `READY_FOR_HUMAN_REVIEW | NOT_READY | BLOCKED`만 사용합니다. `GO`, `SAFE_TO_SIGN`, `계약 가능`은 사용하지 않습니다.

## 9. 물리 아키텍처

배포 단위는 다섯 개로 확정했습니다.

1. `web`
2. `control-api` modular monolith
3. `job-worker`
4. private `mcp-gateway`
5. `asia-northeast3` managed Agent Runtime의 단일 ADK Multi-Agent application

PostgreSQL/PostGIS/pgvector가 제품의 transactional·검색 metadata 저장소이고, object storage가 원문·OCR·packet을, BigQuery가 공공 raw/normalized/curated snapshot을 저장합니다.

`auth`, `project`, `evidence`, `finance`, `decision`, `review`, `packet`을 각각 마이크로서비스로 분해하지 않습니다. 이들은 같은 transaction/CAS 경계를 공유합니다. 검색 전용 서비스는 실제 corpus 규모나 DB I/O·p95 문제가 관측될 때 분리합니다.

Queue는 outbox+Pub/Sub 기반 at-least-once로 운영하며 retry budget, DLQ, heartbeat, progress, checkpoint를 필수화합니다.

Control API가 IAM 인증으로 서울 Agent Runtime을 직접 호출합니다. 생성·embedding model endpoint도 `asia-northeast3`로 고정하고 `global` fallback, 별도 Cloud Run Agent Gateway와 서울에서 미지원인 managed Agent Gateway는 사용하지 않습니다. RAG Engine은 Preview이므로 운영 필수 경로에 두지 않습니다.

Agent Control CLI는 Web과 같은 API·헤드리스 코어를 사용하고 `--json`, 프로젝트·workflow·Evidence·문서 검토·재계산·패킷·source health·진단·스크린샷을 지원합니다.

## 10. 출시 조건

정확도 평균보다 먼저 다음 hard-zero를 만족해야 합니다.

- 프로젝트 간 문서·텍스트·vector·cache·로그 유출 0
- Agent의 외부 계약·송금·대출·신고·연락 0
- 무허가 원문·임베딩 재배포 0
- PII·secret 로그·프롬프트 유출 0
- 미확인 중요 Claim의 계산·Gate·순위 승격 0
- `UNKNOWN → 0` 변환 0
- 오래되거나 충돌한 값을 현재값으로 은닉 0
- anchor·scope·date·revision 불일치 0
- timeout·partial·DLQ를 성공으로 처리 0
- 삭제 후 원문·chunk·embedding·cache·백업 복원 잔존 0

RAG 초기 연구 기준은 Claim stratum별 Recall@50 0.95 이상, 최저 stratum 0.90, material anchor exactness 100%, counterevidence recall 0.95, negative/unknown abstention recall 0.95로 등록합니다. hard-zero는 이 지표로 상쇄할 수 없습니다.

## 11. 전체 구현 의존 순서

이것은 범위 분할이 아니라 전체 제품을 완성하는 구현 순서입니다.

1. Claim/Evidence/CostLine/State/API 계약과 sealed fixture
2. 인증·프로젝트·권한·Reducer·Event·Outbox·CLI
3. 지역 identity·Source Registry·공공데이터 warehouse
4. 개인/프랜차이즈 seed·재무·Fit·Gate·후보 결과
5. 피드백 preview/confirm·선택·이력
6. 문서 격리·OCR/layout/table·Claim review
7. 충돌·dependency graph·선택적 재계산
8. immutable 계약 전 판단자료
9. 공식·프로젝트 corpus와 MCP
10. Hybrid RAG
11. 다섯 Agent의 평가·shadow·canary·advisory 승격
12. SLO·RPO/RTO·DR·retention/delete·운영 자동화

제품 판단 엔진에 관해 지금 추가로 사용자 결정을 받아야 할 사항은 없습니다. 결제 방식·가격·quota와 운영 SLO/RPO/RTO의 숫자는 별도 사업·운영 정책이지만 위 구조를 바꾸지는 않습니다.
