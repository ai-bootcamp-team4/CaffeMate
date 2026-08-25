import type { WorkflowProgress } from './apiClient'

const stageLabels: Record<string, string> = {
  AREA_CONTEXT: '지역 범위와 검색 조건 확인',
  EVIDENCE_PLAN: '자료 조사 계획',
  EVIDENCE_RETRIEVAL: '상권·공식 자료 확인',
  EVIDENCE_ASSESS: '근거 신뢰도 점검',
  PROPOSAL_GENERATION: '창업안 후보 만들기',
  FINANCE_AND_RANK: '비용·현실성 비교',
  CANDIDATE_AUDIT: '후보 교차 점검',
  COMMIT_RESULT: '결과 정리',
}

const terminalLabels: Record<string, string> = {
  QUEUED: '분석을 시작할 준비를 하고 있어요',
  RUNNING: '분석을 진행하고 있어요',
  SUCCEEDED: '분석이 완료됐어요',
  PARTIAL: '확인된 정보까지 분석했어요',
  WAITING_FOR_HUMAN: '추가 확인이 필요해요',
  FAILED: '분석을 완료하지 못했어요',
  CANCELLED: '분석이 취소됐어요',
  STALE: '조건이 바뀌어 새 분석이 필요해요',
}

function workflowStageLabel(stageCode: string): string {
  return stageLabels[stageCode] ?? '분석 단계 처리'
}

export function WorkflowProgressView({ progress, compact = false }: {
  progress: WorkflowProgress
  compact?: boolean
}) {
  const stages = progress.stages ?? []
  const percentage = Math.round((progress.completed_stage_count / Math.max(1, progress.total_stage_count)) * 100)
  const activeCode = progress.current_stage_codes[0]
  const activeLabel = activeCode
    ? workflowStageLabel(activeCode)
    : terminalLabels[progress.status] ?? '분석 상태 확인 중'

  return <section className="workflow-progress-view" data-compact={compact} aria-label="분석 진행 상황" aria-live="polite">
    <div className="workflow-progress-view__summary">
      <strong>{activeLabel}</strong>
      <span>{progress.completed_stage_count}/{progress.total_stage_count} · {percentage}%</span>
    </div>
    <div className="workflow-progress-view__track" role="progressbar" aria-valuemin={0} aria-valuemax={progress.total_stage_count} aria-valuenow={progress.completed_stage_count} aria-valuetext={`${progress.completed_stage_count}/${progress.total_stage_count} 단계 완료`}>
      <span style={{ transform: `scaleX(${percentage / 100})` }} />
    </div>
    {!compact && stages.length > 0 && <ol className="workflow-progress-view__stages">
      {stages.map((stage) => <li key={stage.stage_run_id} data-state={stage.status}>
        <span aria-hidden="true" />
        <span>{workflowStageLabel(stage.stage_code)}</span>
      </li>)}
    </ol>}
  </section>
}