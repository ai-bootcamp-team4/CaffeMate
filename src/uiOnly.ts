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
  Project,
  ResultExplanation,
  ResultView,
  SignedDocumentUpload,
} from './apiClient'
import type { OnboardingValues } from './onboardingState'
import { buildSimulationProject, buildSimulationResult } from './uiSimulation/result'
import { searchSimulationAreas, simulationAreaByToken, type SupportedAreaScenario } from './uiSimulation/scenarios'
import {
  createSimulationWorkflowRegistry,
  feedbackRecalculationStages,
  financialRecalculationStages,
} from './uiSimulation/workflow'
import { applyDocumentScenario, applyPropertyScenario } from './uiSimulation/refinement'
import { buildSeongsuPreparationGuide } from './uiSimulation/preparation'
import {
  applyConditionScenario,
  buildConditionPreview,
  explainSimulationResult,
} from './uiSimulation/assistantScenarios'

const now = '2026-08-25T06:40:00Z'
const projectId = 'project:seongsu-review'
const DOCUMENT_OCR_DELAY_MS = 3_200
const RESULT_LANGUAGE_DELAY_MS = 1_800
const CONDITION_PREVIEW_DELAY_MS = 900

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
  demoControls: { skipActiveWorkflow: () => void }
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
  let documentSequence = 0
  let currentDocumentType: DocumentType = 'PROPERTY_LISTING'
  let currentDocumentRevision: DocumentRevision | null = null
  let currentDocumentForm: DocumentExtractionForm | null = null
  const workflows = createSimulationWorkflowRegistry(project.project_id, options.workflowTimeScale)
  const timeScale = Math.max(0.001, options.workflowTimeScale ?? 1)
  const simulationDelay = (durationMs: number) => new Promise<void>((resolve) => {
    window.setTimeout(resolve, Math.max(1, Math.round(durationMs * timeScale)))
  })

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
      field('frontage', 'FRONTAGE', '전면폭', 6.2, 'm', '점포 조건'),
      field('ceiling-height', 'CEILING_HEIGHT', '천장고', 3.1, 'm', '점포 조건'),
      field('building-use', 'BUILDING_USE', '건축물 표시 용도', '제2종근린생활시설', null, '점포 조건'),
      field('contracted-power', 'CONTRACTED_POWER', '표시 계약전력', 20, 'kW', '설비 조건'),
      field('water-drainage', 'WATER_DRAINAGE', '급배수', '급배수 인입 표시', null, '설비 조건'),
      field('deposit', 'LEASE_DEPOSIT', '보증금', 80_000_000, '원', '임대 조건'),
      field('rent', 'MONTHLY_RENT', '월세', 6_500_000, '원', '임대 조건'),
      field('management', 'MANAGEMENT_FEE', '관리비', 700_000, '원', '임대 조건'),
      field('key-money', 'KEY_MONEY', '권리금', 50_000_000, '원', '임대 조건'),
    ]
    if (documentType === 'COMMERCIAL_LEASE') return [
      field('address', 'ADDRESS', '점포 주소', '서울특별시 성동구 연무장길 57', null, '임대차 목적물'),
      field('area', 'AREA', '면적', 33.1, '㎡', '임대차 목적물'),
      field('floor', 'FLOOR', '층', '1층', null, '임대차 목적물'),
      field('deposit', 'LEASE_DEPOSIT', '보증금', 80_000_000, '원', '임대 조건'),
      field('rent', 'MONTHLY_RENT', '월세', 6_500_000, '원', '임대 조건'),
      field('management', 'MANAGEMENT_FEE', '관리비', 700_000, '원', '임대 조건'),
      field('key-money', 'KEY_MONEY', '권리금', 50_000_000, '원', '임대 조건'),
      field('lease-term', 'LEASE_TERM', '계약기간', '24개월', null, '계약 조건'),
      field('rent-vat', 'VAT_STATUS', '월세 부가세', '별도', null, '계약 조건'),
      field('restoration', 'RESTORATION_CLAUSE', '원상복구', '임차인 부담 조항 있음', null, '특약'),
    ]
    if (documentType === 'EQUIPMENT_QUOTE') return [
      field('equipment-machine', 'QUOTE_LINE_ITEM', '2그룹 에스프레소 머신', 9_800_000, '원', '주요 장비'),
      field('equipment-grinders', 'QUOTE_LINE_ITEM', '그라인더 2대', 3_200_000, '원', '주요 장비'),
      field('equipment-ice', 'QUOTE_LINE_ITEM', '제빙기', 2_400_000, '원', '주요 장비'),
      field('equipment-water', 'QUOTE_LINE_ITEM', '정수·필터 시스템', 900_000, '원', '주요 장비'),
      field('equipment-cold', 'QUOTE_LINE_ITEM', '냉장·냉동고', 2_100_000, '원', '주요 장비'),
      field('equipment-washer', 'QUOTE_LINE_ITEM', '식기세척기', 1_300_000, '원', '주요 장비'),
      field('equipment-pos-small', 'QUOTE_LINE_ITEM', 'POS·소형 장비·바도구', 1_800_000, '원', '기타 장비'),
      field('EQUIPMENT', 'QUOTE_TOTAL', '장비 견적 총액', 21_500_000, '원', '견적 합계'),
      field('equipment-vat', 'VAT_STATUS', '부가세 포함 여부', '포함', null, '견적 조건'),
      field('equipment-install', 'INSTALLATION_STATUS', '설치·시운전', '포함', null, '견적 조건'),
    ]
    if (documentType === 'INTERIOR_QUOTE') return [
      field('interior-demolition', 'QUOTE_LINE_ITEM', '철거·기초 공사', 4_500_000, '원', '공종별 금액'),
      field('interior-mep', 'QUOTE_LINE_ITEM', '전기·급배수 공사', 8_200_000, '원', '공종별 금액'),
      field('interior-finish', 'QUOTE_LINE_ITEM', '목공·도장·마감', 12_800_000, '원', '공종별 금액'),
      field('interior-counter', 'QUOTE_LINE_ITEM', '바·주방 제작', 7_400_000, '원', '공종별 금액'),
      field('interior-light', 'QUOTE_LINE_ITEM', '조명·전기기구', 3_100_000, '원', '공종별 금액'),
      field('interior-hvac', 'QUOTE_LINE_ITEM', '냉난방·환기 보강', 4_500_000, '원', '공종별 금액'),
      field('interior-design', 'QUOTE_LINE_ITEM', '설계·현장관리', 3_000_000, '원', '공종별 금액'),
      field('CONSTRUCTION', 'QUOTE_TOTAL', '인테리어 견적 총액', 43_500_000, '원', '공사비 합계'),
      field('interior_vat', 'VAT_STATUS', '부가세 포함 여부', '포함', null, '견적 조건'),
      field('interior-period', 'CONSTRUCTION_PERIOD', '예상 공사기간', '28일', null, '견적 조건'),
      field('interior-exclusion', 'QUOTE_EXCLUSION', '별도 항목', '간판·소방 증설·전력 증설은 별도', null, '견적 조건'),
    ]
    if (documentType === 'FRANCHISE_DISCLOSURE' || documentType === 'FRANCHISE_AGREEMENT') return [
      field('franchise-reporting-year', 'REPORTING_YEAR', '기준연도', 2025, '년', '정보공개서'),
      field('franchise-fee', 'FRANCHISE_FEE', '가맹비', 7_000_000, '원', '가맹금 구성'),
      field('franchise-training-fee', 'EDUCATION_FEE', '교육비', 2_200_000, '원', '가맹금 구성'),
      field('franchise-deposit', 'FRANCHISEE_DEPOSIT', '가맹보증금', 3_000_000, '원', '가맹금 구성'),
      field('franchise-other-initial', 'OTHER_INITIAL_FEE', '기타 초기비용', 6_000_000, '원', '가맹금 구성'),
      field('FRANCHISE_INITIAL_FEES', 'FRANCHISE_INITIAL_FEE_TOTAL', '가맹 초기비용 총액', 18_200_000, '원', '가맹금'),
      field('franchise-contract-term', 'CONTRACT_TERM', '계약기간', '3년', null, '계약 조건'),
      field('franchise-renewal', 'RENEWAL_CONDITION', '갱신 조건', '계약서 확인 필요', null, '계약 조건'),
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
      await simulationDelay(RESULT_LANGUAGE_DELAY_MS)
      return explainSimulationResult(question, currentResult, candidateId)
    },
    createFeedbackPreview: async (_projectId, input): Promise<FeedbackPreview> => {
      if (!confirmedValues) throw new Error('변경할 현재 창업 조건을 찾을 수 없습니다.')
      await simulationDelay(CONDITION_PREVIEW_DELAY_MS)
      return buildConditionPreview(input, currentResult, confirmedValues)
    },
    confirmFeedback: async (_projectId, preview): Promise<FeedbackResolution> => {
      if (!confirmedValues || !selectedAreaScenario || !currentProject.state) {
        throw new Error('변경안을 적용할 현재 분석 조건을 찾을 수 없습니다.')
      }
      confirmedValues = applyConditionScenario(preview.latest_user_input, confirmedValues)
      const nextStateVersion = currentResult.current_head.state_version + 1
      const nextHead: HeadFence = {
        ...currentResult.current_head,
        state_version: nextStateVersion,
        founder_snapshot_id: `founder-snapshot:seongsu:v${nextStateVersion}`,
      }
      currentProject = buildSimulationProject({
        ...currentProject,
        state: { ...currentProject.state, state_version: nextStateVersion },
      }, selectedAreaScenario, confirmedValues)
      currentResult = buildSimulationResult({
        ...currentResult,
        head: nextHead,
        current_head: nextHead,
      }, selectedAreaScenario, confirmedValues)
      selectedCandidateId = currentResult.primary_candidate_id ?? currentResult.candidates[0]?.candidate_id ?? ''
      const recompute = workflows.start(
        `workflow:feedback:${Date.now()}`,
        currentResult.current_head,
        feedbackRecalculationStages,
      )
      return { preview: { ...preview, status: 'CONFIRMED' }, state_version: nextStateVersion, workflow: recompute }
    },
    cancelFeedback: async (_projectId, previewId): Promise<FeedbackResolution> => {
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
    getPreparationGuide: async (_projectId, selectionId) => buildSeongsuPreparationGuide(
      currentProject,
      selectionId,
      currentResult.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId),
    ),
    applyPropertyTerms: async (_projectId, selectionId, _expectedStateVersion, terms) => {
      if (!confirmedValues) throw new Error('재계산할 창업 조건을 찾을 수 없습니다.')
      const selected = currentResult.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId)
      if (!selected) throw new Error('선택한 후보를 찾을 수 없습니다.')
      const previousFinancialSummary = selected.financial_summary
      currentResult = applyPropertyScenario(currentResult, selectedCandidateId, confirmedValues, terms)
      const workflow = workflows.start(
        `workflow:property-recompute:${Date.now()}`,
        currentResult.current_head,
        financialRecalculationStages,
      )
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
      currentDocumentRevision = { ...currentDocumentRevision, status: 'PARSING', updated_at: now }
      await simulationDelay(DOCUMENT_OCR_DELAY_MS)
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
      workflows.start(recomputeWorkflowRunId, currentResult.current_head, financialRecalculationStages)
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

  return {
    authGateway,
    apiFactory,
    demoControls: { skipActiveWorkflow: () => workflows.skipActive() },
  }
}
