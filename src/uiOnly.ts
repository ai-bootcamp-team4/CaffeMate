import type { AuthGateway, AuthSession } from './auth'
import type {
  AreaSearchResult,
  ControlApiClient,
  DocumentExtractionForm,
  DocumentRevision,
  DocumentType,
  ExtractionFormApplication,
  FeedbackPreview,
  FeedbackResolution,
  HeadFence,
  PreparationGuide,
  Project,
  ResultExplanation,
  ResultView,
  SignedDocumentUpload,
} from './apiClient'
import type { OnboardingValues } from './onboardingState'
import { buildSimulationProject, buildSimulationResult } from './uiSimulation/result'
import { searchSimulationAreas, simulationAreaByToken, type SupportedAreaScenario } from './uiSimulation/scenarios'
import { createSimulationWorkflowRegistry } from './uiSimulation/workflow'
import { applyDocumentScenario, applyPropertyScenario } from './uiSimulation/refinement'

const now = '2026-08-25T06:40:00Z'
const projectId = 'project:seongsu-review'

const head: HeadFence = {
  workflow_generation: 1,
  state_version: 1,
  founder_snapshot_id: 'founder-snapshot:bootstrap',
  area_snapshot_id: 'area-snapshot:bootstrap',
  evidence_snapshot_id: 'evidence-snapshot:bootstrap',
  policy_snapshot_id: 'policy-v1',
  index_generation_id: 'index-generation:20260825',
  seed_registry_id: 'independent-seeds:20260825',
}

const project: Project = {
  project_id: projectId,
  user_id: 'user:local-review',
  created_at: now,
  state: {
    state_version: 1,
    status: 'READY',
    founder: { own_funds_krw: 150_000_000, borrowing_intent: 'NO' },
    area: {
      resolution_status: 'RESOLVED',
      area_id: 'legal-dong:1120011500',
      scope_type: 'LEGAL_DONG',
      legal_dong_code: '1120011500',
      administrative_dong_codes: [],
      mapping_status: 'UNVERIFIED',
      display_name: '서울특별시 성동구 성수동2가',
      coverage_profile: 'R2_REGIONAL_CONNECTOR',
      evidence_ids: ['evidence-area:1120011500'],
      unavailable_fields: ['administrative_dong_mapping'],
    },
    updated_at: now,
  },
}

const initialResult: ResultView = {
  result_bundle_id: 'result-bundle:pending',
  project_id: projectId,
  workflow_run_id: 'workflow:first-proposal:pending',
  head,
  current_head: head,
  primary_candidate_id: null,
  audit_status: 'PASSED',
  outcome_status: 'NO_REVIEWABLE_CANDIDATES',
  created_at: now,
  freshness: 'CURRENT',
  stale_head_dimensions: [],
  invalidation_reason_codes: [],
  candidates: [],
}

export interface UiOnlySimulationOptions {
  workflowTimeScale?: number
}

export function createUiOnlyDependencies(options: UiOnlySimulationOptions = {}): {
  authGateway: AuthGateway
  apiFactory: (session: AuthSession) => ControlApiClient
} {
  const session: AuthSession = {
    uid: 'user:local-review',
    displayName: '사용자',
    getIdToken: async () => 'local-session-token',
    signOut: async () => undefined,
  }

  const authGateway: AuthGateway = {
    restoreSession: async () => null,
    signIn: async () => session,
  }

  let currentProject = { ...project, state: null } as Project
  let currentResult = initialResult
  let selectedAreaScenario: SupportedAreaScenario | null = null
  let confirmedValues: OnboardingValues | null = null
  let selectedCandidateId = ''
  let feedbackStatus: FeedbackPreview['status'] = 'REVIEW_REQUIRED'
  let documentSequence = 0
  let currentDocumentType: DocumentType = 'PROPERTY_LISTING'
  let currentDocumentRevision: DocumentRevision | null = null
  let currentDocumentForm: DocumentExtractionForm | null = null
  const workflows = createSimulationWorkflowRegistry(project.project_id, options.workflowTimeScale)

  const extractionFieldsFor = (documentType: DocumentType): DocumentExtractionForm['fields'] => {
    const field = (
      field_id: string,
      claim_type: string,
      label: string,
      value: string | number | boolean | null,
      unit: string | null,
      section: string,
    ) => ({
      field_id, claim_type, label, raw_value_text: value == null ? null : String(value),
      extracted_value: value, current_value: value, unit, materiality: 'HIGH',
      extraction_status: 'AUTO_FILLED' as const, edit_status: 'UNCHANGED' as const,
      anchor: { page_index: 0, section_path: section }, warnings: [],
    })
    if (documentType === 'PROPERTY_LISTING') return [
      field('address', 'ADDRESS', '점포 주소', '서울특별시 성동구 연무장길 57', null, '매물 정보'),
      field('area', 'AREA', '면적', 33.1, '㎡', '매물 정보'),
      field('floor', 'FLOOR', '층', '1층', null, '매물 정보'),
      field('deposit', 'LEASE_DEPOSIT', '보증금', 80_000_000, '원', '임대 조건'),
      field('rent', 'MONTHLY_RENT', '월세', 6_500_000, '원', '임대 조건'),
      field('management', 'MANAGEMENT_FEE', '관리비', 700_000, '원', '임대 조건'),
      field('key-money', 'KEY_MONEY', '권리금', 50_000_000, '원', '임대 조건'),
    ]
    if (documentType === 'COMMERCIAL_LEASE') return [
      field('area', 'AREA', '면적', 33.1, '㎡', '임대차 목적물'),
      field('floor', 'FLOOR', '층', '1층', null, '임대차 목적물'),
      field('deposit', 'LEASE_DEPOSIT', '보증금', 80_000_000, '원', '임대 조건'),
      field('rent', 'MONTHLY_RENT', '월세', 6_500_000, '원', '임대 조건'),
      field('management', 'MANAGEMENT_FEE', '관리비', 700_000, '원', '임대 조건'),
      field('key-money', 'KEY_MONEY', '권리금', 50_000_000, '원', '임대 조건'),
    ]
    if (documentType === 'EQUIPMENT_QUOTE') return [
      field('EQUIPMENT', 'QUOTE_TOTAL', '장비 견적 총액', 21_500_000, '원', '견적 합계'),
    ]
    if (documentType === 'INTERIOR_QUOTE') return [
      field('CONSTRUCTION', 'QUOTE_TOTAL', '인테리어 견적 총액', 43_500_000, '원', '공사비 합계'),
      field('interior_vat', 'VAT_STATUS', '부가세 포함 여부', '포함', null, '견적 조건'),
    ]
    if (documentType === 'FRANCHISE_DISCLOSURE' || documentType === 'FRANCHISE_AGREEMENT') return [
      field('FRANCHISE_INITIAL_FEES', 'FRANCHISE_INITIAL_FEE_TOTAL', '가맹 초기비용 총액', 18_200_000, '원', '가맹금'),
    ]
    return [field('material_fact', 'MATERIAL_FACT', '확인한 값', '확인된 문서 값', null, '본문')]
  }

  const makeExtractionForm = (revision: DocumentRevision): DocumentExtractionForm => ({
    form_id: `extraction-form:${revision.revision_number}`,
    project_id: project.project_id,
    document_id: revision.document_id,
    document_revision_id: revision.document_revision_id,
    expected_state_version: currentResult.current_head.state_version,
    form_status: 'READY_FOR_REVIEW',
    fields: extractionFieldsFor(revision.document_type),
    apply_label: '반영하고 다시 계산',
    form_digest: `sha256:${String(revision.revision_number).padStart(64, 'a')}`,
    applied_state_version: null,
  })

  const applyConfirmedDocument = (form: DocumentExtractionForm) => {
    if (!confirmedValues || !currentDocumentRevision) throw new Error('문서 재계산에 필요한 현재 조건을 찾을 수 없습니다.')
    currentResult = applyDocumentScenario(
      currentResult,
      selectedCandidateId,
      confirmedValues,
      form,
      currentDocumentType,
      currentDocumentRevision.original_filename,
    )
  }

  const apiFactory = (): ControlApiClient => ({
    createProject: async () => currentProject,
    listProjects: async () => [],
    searchAreas: async (_projectId, query): Promise<AreaSearchResult> => ({
      query,
      status: 'OK',
      completeness: 'UNVERIFIED',
      candidates: searchSimulationAreas(query),
      missing_fields: [],
      source_trace: [],
    }),
    confirmOnboarding: async (_projectId, values, areaSelectionToken) => {
      const area = simulationAreaByToken(areaSelectionToken)
      if (!area) throw new Error('선택한 지역의 분석 데이터가 준비되지 않았습니다. 서울특별시 성동구 성수동1가 또는 성수동2가를 선택해 주세요.')
      selectedAreaScenario = area
      confirmedValues = values
      currentProject = buildSimulationProject(project, area, values)
      currentResult = buildSimulationResult(initialResult, area, values)
      selectedCandidateId = currentResult.primary_candidate_id ?? currentResult.candidates[0]?.candidate_id ?? ''
      return currentProject
    },
    startFirstProposal: async () => {
      if (!selectedAreaScenario || !confirmedValues) throw new Error('분석할 지역과 창업 조건을 먼저 확정해 주세요.')
      const run = workflows.start(`workflow:first-proposal:${Date.now()}`, currentResult.current_head)
      currentResult = { ...currentResult, workflow_run_id: run.workflow_run_id }
      return run
    },
    getWorkflow: async (_projectId, workflowRunId) => workflows.progress(workflowRunId),
    getResult: async () => currentResult,
    explainResult: async (_projectId, _result, question, candidateId): Promise<ResultExplanation> => {
      const selected = currentResult.candidates.find((candidate) => candidate.candidate_id === candidateId)
        ?? currentResult.candidates.find((candidate) => candidate.candidate_id === currentResult.primary_candidate_id)
        ?? currentResult.candidates[0]
      if (!selected) throw new Error('설명할 현재 결과가 없습니다.')
      const gate = selected.decision_trace?.gates.find((item) => item.gate_type === 'CAPITAL')
      const sourceInput = selected.decision_inputs?.find((input) => input.source?.source_ref)
      return {
        explanation_id: `explanation:${Date.now()}`,
        result_bundle_id: currentResult.result_bundle_id,
        candidate_id: selected.candidate_id,
        intent: 'WHY_RECOMMENDED',
        conclusion: selected.summary,
        reasons: gate ? [
          gate.status === 'FAIL'
            ? '현재 자기자금이 최소 초기비용보다 적어 자금 조건을 통과하지 못했습니다.'
            : gate.status === 'CONDITIONAL'
              ? '초기비용 범위가 현재 자기자금과 겹쳐 실제 점포·견적 확인 뒤 자금 조건을 다시 판단해야 합니다.'
              : '현재 자기자금이 초기비용 상단 시나리오까지 감당할 수 있습니다.',
        ] : selected.reason_codes,
        evidence: sourceInput?.source ? [{
          evidence_id: sourceInput.range?.provenance_refs[0] ?? sourceInput.field,
          label: sourceInput.label ?? sourceInput.field,
          value: sourceInput.range?.base == null ? null : `${sourceInput.range.base.toLocaleString('ko-KR')}원`,
          source_title: sourceInput.source.title,
          source_ref: sourceInput.source.source_ref,
          data_date: sourceInput.source.data_date,
          caveat: sourceInput.limitation_code === 'REGIONAL_BENCHMARK_NOT_ACTUAL_PROPERTY' ? '지역 참고값이며 실제 점포의 임대 조건은 아닙니다.' : null,
        }] : [],
        unknowns: selected.next_actions,
        decision_change_conditions: selected.counterfactuals.map((item) => item.condition),
        suggested_action: /바꿔|제외|변경/.test(question) ? 'OPEN_CONDITION_CHANGE' : 'NONE',
        state_changed: false,
      }
    },
    createFeedbackPreview: async (_projectId, input): Promise<FeedbackPreview> => ({
      preview_id: `feedback-preview:${Date.now()}`,
      project_id: currentProject.project_id,
      result_bundle_id: currentResult.result_bundle_id,
      head: currentResult.current_head,
      status: feedbackStatus,
      latest_user_input: input,
      before_founder: { own_funds_krw: currentProject.state?.founder.own_funds_krw ?? 0 },
      after_founder: { own_funds_krw: Math.max(0, Number(currentProject.state?.founder.own_funds_krw ?? 0) - 10_000_000) },
      operations: [],
      clarifying_questions: [],
      affected_stage_codes: ['FINANCE_AND_RANK'],
      risk_flags: [],
      proposal_digest: `sha256:${'a'.repeat(64)}`,
    }),
    confirmFeedback: async (_projectId, preview): Promise<FeedbackResolution> => {
      feedbackStatus = 'CONFIRMED'
      const recompute = workflows.start(`workflow:feedback:${Date.now()}`, currentResult.current_head)
      return { preview: { ...preview, status: 'CONFIRMED' }, state_version: currentResult.current_head.state_version, workflow: recompute }
    },
    cancelFeedback: async (_projectId, previewId): Promise<FeedbackResolution> => {
      feedbackStatus = 'CANCELLED'
      return {
        preview: {
          preview_id: previewId,
          project_id: currentProject.project_id,
          result_bundle_id: currentResult.result_bundle_id,
          head: currentResult.current_head,
          status: 'CANCELLED',
          latest_user_input: '',
          before_founder: {},
          after_founder: null,
          operations: [],
          clarifying_questions: [],
          affected_stage_codes: [],
          risk_flags: [],
          proposal_digest: null,
        },
        state_version: null,
        workflow: null,
      }
    },
    selectCandidate: async (_projectId, _result, candidateId) => {
      selectedCandidateId = candidateId
      const selected = currentResult.candidates.find((candidate) => candidate.candidate_id === candidateId)
      if (!selected) throw new Error('선택한 후보를 찾을 수 없습니다.')
      const requiredEvidence = (selected.decision_inputs ?? [])
        .filter((input) => input.resolution_action && input.resolution_action.type !== 'NONE')
        .map((input) => ({
          code: `${input.resolution_action?.type}:${input.field}`,
          title: input.label ?? input.field,
          status: 'REFINABLE',
          reason: input.resolution_action?.type === 'PROPERTY_TERMS'
            ? '실제 점포 조건을 반영하면 지역 참고값 또는 가정을 교체할 수 있습니다.'
            : '실제 문서를 반영하면 현재 비용 입력을 교체할 수 있습니다.',
        }))
      return {
        selection_id: `selection:${candidateId}`,
        candidate_id: candidateId,
        selected_state_version: currentResult.current_head.state_version,
        required_evidence: requiredEvidence,
        property_intake_enabled: true,
        document_intake_enabled: true,
      }
    },
    getPreparationGuide: async (_projectId, selectionId): Promise<PreparationGuide> => ({
      project_id: currentProject.project_id,
      selection_id: selectionId,
      candidate_id: selectedCandidateId,
      candidate_type: currentResult.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId)?.case_type ?? 'INDEPENDENT',
      jurisdiction_code: currentProject.state?.area.area_id ?? 'area:unknown',
      jurisdiction_display_name: currentProject.state?.area.display_name ?? '선택 지역',
      as_of: '2026-08-25',
      status: 'REVIEW_REQUIRED',
      procedures: [
        {
          procedure_type: 'HYGIENE_EDUCATION', status: 'OK', missing_fields: [], conflicts: [], error_codes: [],
          steps: [{ procedure_type: 'HYGIENE_EDUCATION', step_order: 1, title: '신규 영업자 위생교육 이수', required: true, authority: '식품위생교육기관', source_date: '2026-08-25', evidence_id: 'evidence-procedure:hygiene-education' }],
        },
        {
          procedure_type: 'FOOD_SERVICE_REPORT', status: 'PARTIAL', missing_fields: ['facility_check'], conflicts: [], error_codes: [],
          steps: [{ procedure_type: 'FOOD_SERVICE_REPORT', step_order: 1, title: '휴게음식점 영업신고 준비', required: true, authority: '관할 구청 위생 담당 부서', source_date: '2026-08-25', evidence_id: 'evidence-procedure:food-service-report' }],
        },
      ],
      human_actions_only: true,
      external_submission_performed: false,
      generated_at: now,
    }),
    applyPropertyTerms: async (_projectId, selectionId, _expectedStateVersion, terms) => {
      if (!confirmedValues) throw new Error('재계산할 창업 조건을 찾을 수 없습니다.')
      const selected = currentResult.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId)
      if (!selected) throw new Error('선택한 후보를 찾을 수 없습니다.')
      const previousFinancialSummary = selected.financial_summary
      currentResult = applyPropertyScenario(currentResult, selectedCandidateId, confirmedValues, terms)
      const workflow = workflows.start(`workflow:property-recompute:${Date.now()}`, currentResult.current_head)
      return {
        property_input_id: `property-input:${Date.now()}`,
        project_id: currentProject.project_id,
        selection_id: selectionId,
        candidate_id: selectedCandidateId,
        applied_state_version: currentResult.current_head.state_version,
        terms,
        previous_financial_summary: previousFinancialSummary,
        recompute_workflow: workflow,
        input_kind: 'USER_CONFIRMED_PROPERTY_TERMS' as const,
        is_demo_fixture: false,
        created_at: now,
      }
    },
    beginDocumentUpload: async (_projectId, file, documentType): Promise<SignedDocumentUpload> => {
      documentSequence += 1
      currentDocumentType = documentType
      const documentId = `document:${documentSequence}`
      const revisionId = `document-revision:${documentSequence}`
      currentDocumentRevision = {
        document_id: documentId,
        document_revision_id: revisionId,
        project_id: currentProject.project_id,
        revision_number: documentSequence,
        document_type: documentType,
        original_filename: file.name,
        content_type: file.type,
        size_bytes: file.size,
        sha256: 'a'.repeat(64),
        status: 'UPLOAD_PENDING',
        failure_codes: [],
        created_at: now,
        updated_at: now,
        completed_at: null,
      }
      currentDocumentForm = null
      return {
        document_id: documentId,
        document_revision_id: revisionId,
        revision_number: documentSequence,
        object_path: `projects/${currentProject.project_id}/documents/${documentId}/${file.name}`,
        upload_url: `https://uploads.caffemate.local/${documentId}`,
        method: 'PUT',
        required_headers: { 'Content-Type': file.type },
        expires_at: '2026-08-25T23:59:59Z',
        status: 'UPLOAD_PENDING',
      }
    },
    uploadDocument: async () => undefined,
    completeDocumentUpload: async (_projectId, revisionId): Promise<DocumentRevision> => {
      if (!currentDocumentRevision || currentDocumentRevision.document_revision_id !== revisionId) throw new Error('DOCUMENT_NOT_FOUND')
      currentDocumentRevision = { ...currentDocumentRevision, status: 'EXTRACTION_READY', updated_at: now, completed_at: now }
      currentDocumentForm = makeExtractionForm(currentDocumentRevision)
      return currentDocumentRevision
    },
    getDocumentRevision: async (_projectId, revisionId): Promise<DocumentRevision> => {
      if (!currentDocumentRevision || currentDocumentRevision.document_revision_id !== revisionId) throw new Error('DOCUMENT_NOT_FOUND')
      return currentDocumentRevision
    },
    getDocumentExtractionForm: async (_projectId, revisionId): Promise<DocumentExtractionForm> => {
      if (!currentDocumentForm || currentDocumentForm.document_revision_id !== revisionId) throw new Error('DOCUMENT_EXTRACTION_NOT_READY')
      return currentDocumentForm
    },
    updateDocumentExtractionForm: async (_projectId, form, edits): Promise<DocumentExtractionForm> => {
      const editMap = new Map(edits.map((edit) => [edit.field_id, edit.value]))
      currentDocumentForm = {
        ...form,
        fields: form.fields.map((entry) => editMap.has(entry.field_id)
          ? { ...entry, current_value: editMap.get(entry.field_id) ?? null, edit_status: 'EDITED' as const }
          : entry),
        form_digest: `sha256:${'b'.repeat(64)}`,
      }
      return currentDocumentForm
    },
    applyDocumentExtractionForm: async (_projectId, form): Promise<ExtractionFormApplication> => {
      applyConfirmedDocument(form)
      currentDocumentForm = { ...form, form_status: 'APPLIED', applied_state_version: currentResult.current_head.state_version }
      const recomputeWorkflowRunId = `workflow:document-recompute:${documentSequence}:${Date.now()}`
      workflows.start(recomputeWorkflowRunId, currentResult.current_head)
      return {
        application_id: `document-application:${documentSequence}`,
        project_id: currentProject.project_id,
        document_revision_id: form.document_revision_id,
        applied_state_version: currentResult.current_head.state_version,
        recompute_workflow_run_id: recomputeWorkflowRunId,
        claims: [],
        conflicts: [],
        requires_human_review: false,
      }
    },
  })

  return { authGateway, apiFactory }
}
