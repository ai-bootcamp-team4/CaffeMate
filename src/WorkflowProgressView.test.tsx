import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { WorkflowProgress } from './apiClient'
import { WorkflowProgressView } from './WorkflowProgressView'

afterEach(cleanup)

function progress(): WorkflowProgress {
  const stageCodes = [
    'EVIDENCE_RETRIEVAL',
    'EVIDENCE_ASSESS',
    'PROPOSAL_GENERATION',
    'FINANCE_AND_RANK',
    'CANDIDATE_AUDIT',
    'COMMIT_RESULT',
  ]
  return {
    workflow_run_id: 'workflow-1',
    project_id: 'project-1',
    workflow_code: 'FIRST_PROPOSAL',
    status: 'RUNNING',
    head: {
      workflow_generation: 2,
      state_version: 2,
      founder_snapshot_id: 'founder-2',
      area_snapshot_id: 'area-2',
      evidence_snapshot_id: null,
      policy_snapshot_id: 'policy-1',
      index_generation_id: null,
      seed_registry_id: 'seed-1',
    },
    created_at: '2026-08-25T06:00:00Z',
    updated_at: '2026-08-25T06:00:03Z',
    stages: stageCodes.map((stageCode, index) => ({
      stage_run_id: `stage-${index + 1}`,
      stage_code: stageCode,
      status: index < 3 ? 'SKIPPED' as const : index === 3 ? 'RUNNING' as const : 'PENDING' as const,
      attempt: index === 3 ? 1 : 0,
      reason_codes: [],
      failure_code: null,
      updated_at: '2026-08-25T06:00:03Z',
      completed_at: index < 3 ? '2026-08-25T06:00:02Z' : null,
    })),
    completed_stage_count: 3,
    total_stage_count: 6,
    current_stage_codes: ['FINANCE_AND_RANK'],
    terminal_reason_codes: [],
    human_review_requests: [],
    poll_after_ms: 750,
  }
}

describe('WorkflowProgressView', () => {
  it('renders authoritative order, progress value, and skipped-stage meaning', () => {
    render(<WorkflowProgressView progress={progress()} />)

    expect(screen.getAllByText('비용·현실성 비교')).toHaveLength(2)
    expect(screen.getByText('3/6 · 50%')).toBeTruthy()
    const bar = screen.getByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('3')
    expect(bar.getAttribute('aria-valuemax')).toBe('6')
    expect(screen.getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      '상권·공식 자료 확인 · 이번 재계산에서는 생략',
      '근거 신뢰도 점검 · 이번 재계산에서는 생략',
      '창업안 후보 만들기 · 이번 재계산에서는 생략',
      '비용·현실성 비교',
      '후보 교차 점검',
      '결과 정리',
    ])
  })
})