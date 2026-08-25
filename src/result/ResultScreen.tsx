import { useEffect, useRef, useState } from 'react'
import {
  waitForWorkflow,
  type CandidateSelection,
  type ControlApiClient,
  type DecisionInput,
  type PreparationGuide,
  type Project,
  type PropertyTermsInput,
  type ResultCandidate,
  type ResultView,
  type WorkflowProgress,
  type WorkflowRun,
} from '../apiClient'
import { candidateSource, internalLabel, userError } from '../presentation'
import { Badge } from '../ui/Badge'
import {
  NumericRefinementFlow,
  type DocumentRecalculation,
  type PropertyRecalculation,
} from '../refinement/NumericRefinementFlow'
import { CandidateComparison } from './CandidateComparison'
import { DecisionReasons } from './DecisionReasons'
import { ExternalChecks } from './ExternalChecks'
import { FinanceBreakdown } from './FinanceBreakdown'
import { MarketContext } from './MarketContext'
import { FeedbackPanel } from './ResultAssistant'
import { ResultHero } from './ResultHero'
import { ResultSectionNav } from './ResultSectionNav'
import { StartupPreparation } from './StartupPreparation'
import { WorkflowProgressView } from '../WorkflowProgressView'
import './Result.css'

interface SelectionContext {
  selection: CandidateSelection
  result: ResultView
  candidate: ResultCandidate
}

interface RecalculationView {
  title: string
  description: string
  progress: WorkflowProgress | null
}

export function ResultScreen({ client, project, initialResult }: {
  client: ControlApiClient
  project: Project
  initialResult: ResultView
}) {
  const [result, setResult] = useState(initialResult)
  const [activeCandidateId, setActiveCandidateId] = useState(
    initialResult.primary_candidate_id ?? initialResult.candidates[0]?.candidate_id ?? '',
  )
  const [selection, setSelection] = useState<CandidateSelection | null>(null)
  const [selectionBusy, setSelectionBusy] = useState(false)
  const [refinementTarget, setRefinementTarget] = useState<DecisionInput | null>(null)
  const [refinementError, setRefinementError] = useState('')
  const [preparationGuide, setPreparationGuide] = useState<PreparationGuide | null>(null)
  const [preparationBusy, setPreparationBusy] = useState(false)
  const [preparationError, setPreparationError] = useState('')
  const [recalculation, setRecalculation] = useState<RecalculationView | null>(null)
  const resultScrollYRef = useRef(0)
  const recalculationActive = recalculation !== null

  useEffect(() => {
    if (!recalculationActive) return

    const documentElement = document.documentElement
    const body = document.body
    const previousDocumentElementOverflow = documentElement.style.overflow
    const previousBodyOverflow = body.style.overflow
    documentElement.style.overflow = 'hidden'
    body.style.overflow = 'hidden'

    return () => {
      documentElement.style.overflow = previousDocumentElementOverflow
      body.style.overflow = previousBodyOverflow
    }
  }, [recalculationActive])

  const waitForResultWorkflow = async (
    workflow: WorkflowRun,
    title: string,
    description: string,
  ) => {
    setRecalculation({ title, description, progress: null })
    window.scrollTo({ top: 0 })
    await new Promise<void>((resolve) => window.setTimeout(resolve, 0))
    const terminal = await waitForWorkflow(client, project.project_id, workflow, (progress) => {
      setRecalculation({ title, description, progress })
    })
    await new Promise<void>((resolve) => window.setTimeout(resolve, 0))
    if (!['SUCCEEDED', 'PARTIAL'].includes(terminal.status)) {
      setRecalculation(null)
      throw new Error(`재계산이 완료되지 않았습니다: ${internalLabel(terminal.status)}`)
    }
  }

  const finishRecalculation = () => setRecalculation(null)

  const candidates = result.candidates
  const activeCandidate = candidates.find((candidate) => candidate.candidate_id === activeCandidateId) ?? candidates[0]
  const createdAt = new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(result.created_at))

  const ensureSelection = async (): Promise<SelectionContext> => {
    if (!activeCandidate) throw new Error('선택한 창업안이 없습니다.')
    if (selection && selection.candidate_id === activeCandidate.candidate_id && result.freshness === 'CURRENT') {
      return { selection, result, candidate: activeCandidate }
    }

    let selectionResult = result
    let selectionCandidate = activeCandidate
    if (selectionResult.freshness === 'STALE') {
      const workflow = await client.startFirstProposal(project.project_id)
      const terminal = await waitForWorkflow(client, project.project_id, workflow)
      if (terminal.status !== 'SUCCEEDED') throw new Error('최신 조건 반영을 완료하지 못했습니다.')
      const refreshed = await client.getResult(project.project_id)
      const source = candidateSource(activeCandidate)
      const refreshedCandidate = refreshed.candidates.find((candidate) => candidateSource(candidate) === source)
      setResult(refreshed)
      if (!refreshedCandidate) {
        setActiveCandidateId(refreshed.candidates[0]?.candidate_id ?? '')
        throw new Error('선택한 창업안은 최신 조건에서 제외됐습니다. 새 결과를 확인해 주세요.')
      }
      setActiveCandidateId(refreshedCandidate.candidate_id)
      selectionResult = refreshed
      selectionCandidate = refreshedCandidate
    }

    const nextSelection = await client.selectCandidate(
      project.project_id,
      selectionResult,
      selectionCandidate.candidate_id,
    )
    setSelection(nextSelection)
    return { selection: nextSelection, result: selectionResult, candidate: selectionCandidate }
  }

  const openRefinement = async (input: DecisionInput) => {
    resultScrollYRef.current = window.scrollY
    setSelectionBusy(true)
    setRefinementError('')
    try {
      const context = await ensureSelection()
      const currentTarget = context.candidate.decision_inputs?.find((candidateInput) => candidateInput.field === input.field) ?? input
      setRefinementTarget(currentTarget)
      window.scrollTo({ top: 0 })
    } catch (error) {
      setRefinementError(userError(error, '실제값 입력 화면을 준비하지 못했습니다.'))
    } finally {
      setSelectionBusy(false)
    }
  }

  const loadPreparationGuide = async () => {
    setPreparationBusy(true)
    setPreparationError('')
    try {
      const context = await ensureSelection()
      setPreparationGuide(await client.getPreparationGuide(project.project_id, context.selection.selection_id))
    } catch (error) {
      setPreparationError(userError(error, '공식 절차를 불러오지 못했습니다. 같은 화면에서 다시 확인해 주세요.'))
    } finally {
      setPreparationBusy(false)
    }
  }

  const applyPropertyTerms = async (terms: PropertyTermsInput): Promise<PropertyRecalculation> => {
    if (!selection || !activeCandidate) throw new Error('선택한 창업안이 없습니다.')
    const application = await client.applyPropertyTerms(
      project.project_id,
      selection.selection_id,
      selection.selected_state_version,
      terms,
    )
    try {
      await waitForResultWorkflow(
        application.recompute_workflow,
        '입력한 점포 조건으로 다시 계산하고 있어요',
        '지역 조사와 후보 생성은 유지하고, 바뀐 비용과 판정에 필요한 단계만 다시 확인합니다.',
      )
      const nextResult = await client.getResult(project.project_id)
      if (nextResult.freshness !== 'CURRENT') throw new Error('점포 조건 반영 결과를 최신 상태로 저장하지 못했습니다.')
      const source = candidateSource(activeCandidate)
      const nextCandidate = nextResult.candidates.find((candidate) => candidateSource(candidate) === source)
      if (!nextCandidate) throw new Error('재계산된 후보를 찾지 못했습니다.')
      setResult(nextResult)
      setActiveCandidateId(nextCandidate.candidate_id)
      setSelection((current) => current ? { ...current, selected_state_version: application.applied_state_version } : current)
      setPreparationGuide(null)
      finishRecalculation()
      return { mode: 'LIVE', application, candidate: nextCandidate, result: nextResult }
    } catch (error) {
      finishRecalculation()
      throw error
    }
  }

  const refreshAfterDocument = async (): Promise<DocumentRecalculation> => {
    if (!activeCandidate) throw new Error('선택한 창업안이 없습니다.')
    const previousFinancialSummary = activeCandidate.financial_summary
    const source = candidateSource(activeCandidate)
    const nextResult = await client.getResult(project.project_id)
    if (nextResult.freshness !== 'CURRENT') throw new Error('문서 반영 결과를 최신 상태로 저장하지 못했습니다.')
    const nextCandidate = nextResult.candidates.find((candidate) => candidateSource(candidate) === source) ?? nextResult.candidates[0]
    if (!nextCandidate) throw new Error('문서 반영 뒤 후보를 찾지 못했습니다.')
    setResult(nextResult)
    setActiveCandidateId(nextCandidate.candidate_id)
    setSelection((current) => current ? { ...current, selected_state_version: nextResult.current_head.state_version } : current)
    setPreparationGuide(null)
    const next = { candidate: nextCandidate, result: nextResult, previousFinancialSummary }
    finishRecalculation()
    return next
  }

  const updateResult = (next: ResultView) => {
    setResult(next)
    setActiveCandidateId(next.primary_candidate_id ?? next.candidates[0]?.candidate_id ?? '')
    setSelection(null)
    setRefinementTarget(null)
    setPreparationGuide(null)
    setPreparationError('')
    finishRecalculation()
  }

  const changeCandidate = (candidateId: string) => {
    setActiveCandidateId(candidateId)
    setSelection(null)
    setRefinementTarget(null)
    setPreparationGuide(null)
    setPreparationError('')
  }

  const closeRefinement = () => {
    const restoreTop = resultScrollYRef.current
    setRefinementTarget(null)
    window.setTimeout(() => window.scrollTo({ top: restoreTop }), 0)
  }

  if (!activeCandidate) {
    return <main className="analysis-stage"><h1>분석할 창업안을 만들지 못했어요</h1><p>희망 지역과 자금 조건을 확인한 뒤 다시 분석해 주세요.</p></main>
  }

  const topbar = (
    <header className="topbar">
      <a className="wordmark" href={refinementTarget ? '#refinementTop' : '#top'}>CaffeMate</a>
      <div className="topbar__meta">
        <Badge tone={result.freshness === 'CURRENT' ? 'success' : 'warning'}>{internalLabel(result.freshness)}</Badge>
        <span className="version">결과 생성 {createdAt}</span>
      </div>
    </header>
  )

  const assistant = (
    <div className="result-assistant-wrap result-assistant-dock" data-testid="result-assistant-dock">
      <FeedbackPanel
        key={`${result.result_bundle_id}-${activeCandidate.candidate_id}`}
        client={client}
        projectId={project.project_id}
        result={result}
        candidate={activeCandidate}
        onResult={updateResult}
        onRecompute={(workflow) => waitForResultWorkflow(
          workflow,
          '바뀐 조건으로 결과를 다시 계산하고 있어요',
          '지역 자료는 그대로 두고, 후보 구성과 비용·판정처럼 영향을 받는 단계만 다시 확인합니다.',
        )}
        onRecomputeFinished={finishRecalculation}
      />
    </div>
  )

  const recalculationScreen = recalculation ? (
    <div className="shell min-h-dvh recalculation-screen">
      <header className="topbar"><a className="wordmark" href="#recalculationTop">CaffeMate</a></header>
      <main className="analysis-stage" id="recalculationTop" aria-live="polite">
        <div className="analysis-stage__pulse" aria-hidden="true"><span /><span /><span /></div>
        <p className="stage-label">결과 업데이트</p>
        <h1>{recalculation.title}</h1>
        <p>{recalculation.description}</p>
        {recalculation.progress && <WorkflowProgressView progress={recalculation.progress} />}
      </main>
    </div>
  ) : null

  if (selection && refinementTarget) {
    return (
      <div className="shell min-h-dvh">
        {recalculationScreen}
        <div className="result-screen-underlay" aria-hidden={Boolean(recalculation)} inert={recalculation ? true : undefined}>
          {topbar}
          <NumericRefinementFlow
            client={client}
            projectId={project.project_id}
            candidate={activeCandidate}
            selection={selection}
            target={refinementTarget}
            onBack={closeRefinement}
            onApplyProperty={applyPropertyTerms}
            onDocumentApplied={refreshAfterDocument}
            onDocumentRecompute={(workflow) => waitForResultWorkflow(
              workflow,
              '확인한 문서 값으로 다시 계산하고 있어요',
              '문서에서 확인한 비용이 영향을 주는 계산과 판정 단계만 다시 확인합니다.',
            )}
            onRecomputeFinished={finishRecalculation}
          />
          {assistant}
          <footer className="footer"><strong>CaffeMate</strong><span>계약과 최종 창업 결정을 대신하지 않습니다.</span></footer>
        </div>
      </div>
    )
  }

  return (
    <div className="shell min-h-dvh">
      {recalculationScreen}
      <div className="result-screen-underlay" aria-hidden={Boolean(recalculation)} inert={recalculation ? true : undefined}>
        {topbar}
        <main className="page result-page" id="top">
          {result.freshness === 'STALE' && (
            <div className="demo-notice" role="note"><span className="demo-notice__mark">!</span><p>입력 조건이 바뀌었어요. 실제값을 반영하기 전에 최신 조건으로 다시 계산합니다.</p></div>
          )}
          <ResultHero candidate={activeCandidate} />
          <ResultSectionNav
            showCandidates={candidates.length > 1}
            showMarket={(activeCandidate.market_signals ?? []).some((signal) => signal.decision_role === 'CONTEXT_ONLY')}
            showExternal={(activeCandidate.verification_requirements?.length ?? 0) > 0}
          />
          <CandidateComparison
            candidates={candidates}
            activeCandidateId={activeCandidate.candidate_id}
            onSelect={changeCandidate}
          />
          <DecisionReasons candidate={activeCandidate} />
          <FinanceBreakdown candidate={activeCandidate} onRefine={(input) => void openRefinement(input)} busy={selectionBusy} />
          {refinementError && <p className="result-action-error" role="alert">{refinementError}</p>}
          <MarketContext candidate={activeCandidate} />
          <ExternalChecks candidate={activeCandidate} />
          <StartupPreparation
            key={activeCandidate.candidate_id}
            guide={preparationGuide}
            busy={preparationBusy}
            error={preparationError}
            onLoad={() => void loadPreparationGuide()}
          />
        </main>
        {assistant}
        <footer className="footer"><strong>CaffeMate</strong><span>계약과 최종 창업 결정을 대신하지 않습니다.</span><span>현재 입력과 확인된 자료를 기준으로 계산했어요.</span></footer>
      </div>
    </div>
  )
}
