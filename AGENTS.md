# CaffeMate GitHub 협업 규칙

이 `AGENTS.md`를 깃허브 협업 규칙의 정본으로 사용한다. 모든 작업을 시작할 때 반드시 먼저 읽는다.

## 먼저 읽기

1. `docs/product-spec.md` — 제품 행동 정본
2. `docs/contracts/agent-runtime-protocol.md` — 백엔드·Agent Runtime·MCP 연결 계약
3. 담당 영역과 직접 연결된 `docs/architecture/**` 문서와 JSON Schema

## 담당과 소유권

| 담당 | 주 소유 영역 | 하지 않는 일 |
| --- | --- | --- |
| 유시우 (`siwoo-you`) | `src/**`, 온보딩·상권·결과·피드백·문서 폼 UI, 공개 API client와 화면 상태 | Agent Runtime·MCP 직접 호출, 프론트에서 비용·Gate·순위 확정 |
| 김민석 (`Minseok Kim`) | `api/**`, 권위 State·Event·Workflow·reducer, 공개·내부 API, 인증·프로젝트 격리, MCP scope token 발급과 client, 결정론적 재무·Gate·순위, `worker/**`의 queue·lease·retry·배포 수명주기, GCP 통합 | Agent 자연어 출력을 검증 없이 저장, 모델에 권위 계산 위임, MCP server 내부 connector 구현 |
| 이민우 (`eocodn`) | `agents/**`, `mcp/**`, `rag/**`, `worker/pipelines/parsing/**`, `worker/pipelines/indexing/**`, deterministic Agent dispatcher·프롬프트·DTO, MCP tool server·manifest, parser·embedding·index generation, 검색·근거 연결, Agent·RAG 평가 | persistent State write, 비용·Gate·순위 확정, 사용자용 API·scope token 발급·Workflow lease 직접 변경 |

아직 존재하지 않는 디렉터리는 해당 영역 구현을 시작할 때 위 이름으로 만든다. 실제 파일 구조가 바뀌면 먼저 이 소유권 표와 연결 계약을 함께 갱신한다.

## 공동 계약 경계

- 프론트엔드는 공개 Control API만 호출한다.
- Agent는 현재 State snapshot을 읽어 typed proposal을 만들고 MCP는 read result만 반환한다. 둘 다 권위 State를 직접 수정하지 않으며, 최종 검증과 persistent write는 백엔드 reducer만 수행한다.
- `docs/contracts/**`와 양쪽에서 공유하는 생성 타입은 김민석·이민우가 함께 검토한다. 공개 응답이 바뀌면 유시우도 검토한다.
- 계약 변경은 producer가 Schema·fixture·consumer 영향까지 같은 풀 리퀘스트에 포함하고, 반대쪽 owner가 실제 consumer와 대조해 승인한다.
- 경계가 불명확하면 코드를 먼저 맞추지 말고 `docs/contracts/agent-runtime-protocol.md`와 JSON Schema를 먼저 수정한다.

| 계약 | Producer | Consumer·최종 검증 |
| --- | --- | --- |
| 공개 API request·response | 김민석 | 유시우 |
| `AgentTask`·id pool·full head | 김민석 | 이민우의 dispatcher |
| `AgentTaskResult`·role payload | 이민우 | 김민석의 boundary validator |
| MCP request·scope token | 김민석 | 이민우의 MCP server |
| MCP manifest·`structuredContent` | 이민우 | 김민석의 MCP client |
| ParserBlock·IndexGeneration·RetrievalResult | 이민우 | 김민석의 Evidence validator·Workflow |

```text
React Web
→ Control API
→ Agent Runtime 또는 MCP
→ typed result
→ Control API validation·calculation·reducer
→ React Web
```

## 배포 계약 정합성

- `agents/**`, `mcp/**`, `rag/**`, `api/app/agents/**`, 공유 스키마 또는 런타임 프로토콜이 바뀌면 영향받는 API·MCP·Agent Runtime을 같은 불변 `origin/main` 리비전으로 빌드하고 배포한다.
- 이전 소스 리비전이나 이전 이미지로 실행한 카나리 성공은 현재 배포의 검증 근거로 사용하지 않는다.
- 배포 완료 전에 각 운영 리소스의 `source-revision`·이미지 digest를 다시 읽고, 서로 호환되는 승인 조합인지 확인한다.
- MCP의 HTTP `200`은 충분한 검증이 아니다. 최신 API 이미지로 실행한 카나리에서 `structuredContent`, `EvidenceRecord`, Agent 입출력 스키마와 최종 제안 결과까지 확인한다.
- 카나리 Job은 영향받는 운영 서비스와 동일한 이미지·환경 변수·Secret 연결을 사용해야 하며, 구성 누락으로 실패하면 제품 실패와 구분해 보고한다.
- 관련 런타임 중 하나라도 리비전 정합성 또는 최신 이미지 카나리가 확인되지 않으면 `배포 중` 또는 `검증 대기` 상태로 보고하고 완료로 표현하지 않는다.

## 협업 방식

짧게 유지되는 기능 브랜치를 사용하는 Trunk-based GitHub Flow를 따른다.

1. 모든 작업 전에 원격 `main`이 최신인지 확인하고 로컬 기준을 최신화한다.
2. 최신 `main`에서 작업 목적별 기능 브랜치를 만든다. `main`에 직접 커밋하지 않는다.
3. 변경 사항은 검토 가능한 작업 단위로 나누어 커밋한다.
4. 풀 리퀘스트를 열기 전에 커밋과 변경 범위를 정리하고 필요한 경우 스쿼시한다.
5. 검토가 끝난 풀 리퀘스트는 `Squash and merge`로 `main`에 합친다.
