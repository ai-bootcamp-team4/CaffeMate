import type { Project } from './apiClient'

function formatWon(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${new Intl.NumberFormat('ko-KR').format(value)}원`
    : '아직 입력하지 않음'
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('ko-KR', {
    dateStyle: 'medium',
    timeZone: 'Asia/Seoul',
  }).format(new Date(value))
}

function projectStatus(project: Project) {
  if (!project.state) return '입력 중'
  if (project.state.status === 'RESULT_READY') return '결과 준비됨'
  if (project.state.status === 'ANALYZING') return '분석 이어서'
  return '저장됨'
}

export default function ProjectChooser({
  projects,
  busyProjectId,
  creating,
  error,
  onResume,
  onCreate,
}: {
  projects: Project[]
  busyProjectId: string | null
  creating: boolean
  error: string
  onResume: (project: Project) => void
  onCreate: () => void
}) {
  const ordered = [...projects].sort((left, right) => right.created_at.localeCompare(left.created_at))

  return <div className="project-catalogue">
    <header className="project-catalogue__nav">
      <strong className="wordmark">CaffeMate</strong>
      <button className="btn btn--primary" type="button" onClick={onCreate} disabled={creating || busyProjectId !== null} aria-busy={creating}>
        {creating ? '새 분석 준비 중' : '새 분석 만들기'}
      </button>
    </header>

    <main className="project-catalogue__main">
      <header className="project-catalogue__head">
        <p>저장된 창업 검토</p>
        <h1>이어서 살펴볼 카페 창업안을 선택하세요.</h1>
        <span>지역과 자금, 분석 결과는 프로젝트별로 따로 보관됩니다.</span>
      </header>

      {error && <p className="field__message" data-tone="error" role="alert">{error}</p>}

      <section className="project-list" aria-label={`저장된 창업 검토 ${ordered.length}개`}>
        <div className="project-list__labels" aria-hidden="true">
          <span>희망 지역</span><span>자기자금</span><span>진행 상태</span><span>최근 프로젝트</span><span />
        </div>
        {ordered.map((project) => {
          const isBusy = busyProjectId === project.project_id
          return <article className="project-row" key={project.project_id}>
            <div className="project-row__primary">
              <strong>{project.state?.area.display_name ?? '지역 입력 전'}</strong>
              <span>{formatDate(project.created_at)}에 시작</span>
            </div>
            <dl>
              <div><dt>자기자금</dt><dd>{formatWon(project.state?.founder.own_funds_krw)}</dd></div>
              <div><dt>진행 상태</dt><dd>{projectStatus(project)}</dd></div>
              <div><dt>저장 기준</dt><dd>{project.state ? `${project.state.state_version}번째 변경` : '첫 입력 전'}</dd></div>
            </dl>
            <button className="btn btn--accent" type="button" onClick={() => onResume(project)} disabled={creating || busyProjectId !== null} aria-busy={isBusy}>
              {isBusy ? '불러오는 중' : project.state ? '이어보기' : '입력 이어서'}
            </button>
          </article>
        })}
      </section>
    </main>

    <footer className="project-catalogue__footer">
      <strong>CaffeMate</strong><span>계약과 최종 창업 결정을 대신하지 않습니다.</span>
    </footer>
  </div>
}
