# 대한민국 법정동 디렉터리

`legal-dongs-20260301.tsv`는 행정안전부가 공개한 2026년 3월 1일 시행
`jscode20260301.zip`의 `KIKcd_B.20260301`에서 현재 사용 중인 법정동·리 행만 추출한
런타임 조회 자료다.

- 원문: https://www.mois.go.kr/frt/bbs/type001/commonSelectBoardArticle.do?bbsId=BBSMSTR_000000000052&nttId=124059
- 게시일: 2026-02-26
- 기준일: 2026-03-01
- 파일 형식: `법정동코드`, `시도명`, `시군구명`, `읍면동명`, `동리명`, `생성일자`의 탭 구분 값
- 범위: 말소일이 없고 읍면동명 또는 동리명이 있는 20,279개 행
- 파생 파일 SHA-256: `a4e216a7f1af44b8b5f2276e6043e74f251a105474c21871db3406407ba4d377`

이 자료는 동네 이름을 법정동 후보로 빠르게 찾는 용도다. 행정동 관계는 별도 공식 매핑을
확인하기 전까지 `UNVERIFIED`로 유지한다. 자료 기준일 이후의 변경은 자동으로 추측하지 않고,
새 행정안전부 원본을 검증한 뒤 버전이 붙은 새 파일로 교체한다.

## 프랜차이즈 카탈로그와 RAG 근거 목록

두 파일은 목적이 다르다.

- `franchise-brands-20260823.json`: 필터와 계산기가 읽는 구조화 카탈로그
- `franchise-rag-sources-20260824.json`: Vertex AI RAG Engine에 넣을 공식 원문의 수집 목록과 메타데이터

구조화 카탈로그에는 개인 가맹 여부와 공식 페이지에서 확인한 비용 범위만 둔다. 비용을
확인하지 못한 브랜드는 숫자를 추정하지 않고 `UNKNOWN`으로 둔다. 공식 페이지가 제시한
일부 항목의 합계도 전체 창업비로 바꾸지 않고 `PARTIAL`로 표시하며, 보증금·권리금·별도
공사처럼 빠진 비용을 `missing_costs`에 남긴다.

`list_franchise_universe`는 다음 조건을 모두 만족하는 브랜드만 Proposal Agent에 보낸다.

1. `individual_franchise_eligibility`가 `VERIFIED`
2. `proposal_eligible`이 `true`
3. `usage`가 `PROPOSAL_CANDIDATE`

스타벅스와 커피빈처럼 개인 가맹 대상이 아닌 브랜드는 `INELIGIBLE`과
`COMPETITOR_REFERENCE`로 보존하지만 추천 후보에는 포함하지 않는다. 현재 확인된 개인 가맹
후보는 이디야커피, 메가MGC커피, 컴포즈커피, 빽다방, 더벤티, 커피베이, 할리스,
투썸플레이스, 매머드커피다.

RAG 근거 목록의 `ingestion_status: READY`는 수집 대상과 원문 위치가 준비됐다는 뜻이다.
Vertex AI RAG Engine에 실제 수집됐다는 뜻이 아니다. 수집 작업이 성공한 뒤 별도 운영
메타데이터로 상태를 기록해야 한다. 원문이 게시일이나 기준일을 표시하지 않으면
`published_or_data_date`는 확인일로 대신 채우지 않고 `null`로 둔다.

- 카탈로그 기준일: 2026-08-23
- 근거 확인일: 2026-08-24
- 정보공개서 연결 상태: 모든 후보 `MISSING`
- 지역 출점 가능성: 본사 확인 전까지 누락 상태

특정 지역의 출점 승인, 상권 보호, 최신 정보공개서 완전성, 실제 점포의 임대차 조건과
총투자비는 이 파일만으로 확정할 수 없다.

### 세 브랜드 공식 RAG 색인 절차

현재 최소 연결 대상은 컴포즈커피, 메가MGC커피, 이디야커피다. 브랜드마다 `개인 가맹
가능 여부`와 `공식 창업비 안내`를 분리해 모두 여섯 개 문서를 준비한다.

- 정제 문서: `rag/data/franchise-official/<brand_id>/{eligibility,opening-cost}.md`
- 색인 등록부: `franchise-rag-file-registry-20260825.json`
- 대상 corpus: `projects/proj-aj20-211200020328/locations/asia-northeast3/ragCorpora/5148740273991319552`

운영 반영은 다음 순서로 한다.

1. 여섯 파일을 등록부의 `sourceUri`와 정확히 같은 Cloud Storage 경로에 올린다.
2. `POST https://asia-northeast3-aiplatform.googleapis.com/v1beta1/{corpus}/ragFiles:import`로
   여섯 경로를 가져온다. 고정 길이 청크는 512자, 중첩은 100자로 둔다.
3. 장기 작업이 끝나면 `GET .../v1beta1/{corpus}/ragFiles?pageSize=100`으로 파일을 다시
   읽어 `gcsSource.uris`와 `sourceUri`를 대조한다.
4. 확인한 파일 ID를 등록부의 `ragFileId`에 기록한다. 여섯 ID가 모두 기록되기 전에는
   `COMPANY_OFFICIAL_FRANCHISE`의 파일 ID 기반 정확 검색을 활성화하지 않는다.
5. 집중 테스트를 실행한 뒤 `source URL`, 기준일, source family, claim type이
   `EvidenceRecord`와 결과 카드 인용에 그대로 남는지 확인한다.

필요한 권한은 Cloud Storage 객체 생성·조회, `aiplatform.ragFiles.import`,
`aiplatform.ragFiles.list`, `aiplatform.ragFiles.get`이다. Vertex AI 서비스 에이전트에는
대상 객체 조회 권한이 있어야 한다. 색인 전 준비 문서의 앞부분 메타데이터가 Vertex의
검색 메타데이터로 자동 변환된다고 가정하지 않는다. 실제 파일 ID를 등록부에 고정해 검색
범위를 정하고, 등록부가 URL·기준일·claim type을 복원한다.

공식 API 기준:

- [RAG 파일 가져오기](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/v1beta1/projects.locations.ragCorpora.ragFiles/import)
- [RAG context 검색과 파일 ID 범위](https://docs.cloud.google.com/vertex-ai/docs/reference/rest/v1beta1/projects.locations/retrieveContexts)
