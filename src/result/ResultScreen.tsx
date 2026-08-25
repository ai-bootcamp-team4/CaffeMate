import { useState } from 'react'
import { waitForWorkflow, type CandidateSelection, type ControlApiClient, type PreparationGuide, type Project, type PropertyTermsInput, type ResultView } from '../apiClient'
import { candidateSource, internalLabel, userError } from '../presentation'
import { Badge } from '../ui/Badge'
import { VerificationFlow, type PropertyRecalculation } from '../verification/VerificationFlow'
import { CandidateComparison } from './CandidateComparison'
import { Counterfactuals } from './Counterfactuals'
import { DecisionReasons } from './DecisionReasons'
import { ExternalChecks } from './ExternalChecks'
import { FinanceBreakdown } from './FinanceBreakdown'
import { MarketContext } from './MarketContext'
import { RefinableUnknowns } from './RefinableUnknowns'
import { FeedbackPanel } from './ResultAssistant'
import { ResultHero } from './ResultHero'
import './Result.css'

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
  const [actionStatus, setActionStatus] = useState('결론과 근거를 읽은 뒤 다음 검증 단계를 선택해 주세요.')
  const [selectionBusy, setSelectionBusy] = useState(false)
  const [verificationOpen, setVerificationOpen] = useState(false)
  const [preparationGuide, setPreparationGuide] = useState<PreparationGuide | null>(null)
  const [preparationBusy, setPreparationBusy] = useState(false)
  const [preparationError, setPreparationError] = useState('')
  const [feedbackSuggestion, setFeedbackSuggestion] = useState('')

  const candidates = result.candidates
  const activeCandidate = candidates.find((candidate) => candidate.candidate_id === activeCandidateId) ?? candidates[0]
  const createdAt = new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(result.created_at))

  const loadPreparationGuide = async (nextSelection: CandidateSelection) => {
    setPreparationBusy(true)
    setPreparationError('')
    try {
      setPreparationGuide(await client.getPreparationGuide(project.project_id, nextSelection.selection_id))
    } catch (error) {
      setPreparationError(userError(error, '공식 절차를 불러오지 못했습니다. 같은 단계에서 다시 확인해 주세요.'))
    } finally {
      setPreparationBusy(false)
    }
  }

  const select = async () => {
    if (!activeCandidate) return
    setSelectionBusy(true)
    try {
      let selectionResult = result
      let selectionCandidate = activeCandidate
      if (selectionResult.freshness === 'STALE') {
        setActionStatus('변경된 조건으로 창업안을 다시 계산하고 있어요.')
        const workflow = await client.startFirstProposal(project.project_id)
        const terminal = await waitForWorkflow(client, project.project_id, workflow, (progress) =>
          setActionStatus(`최신 조건 반영 ${progress.completed_stage_count}/${progress.total_stage_count}`),
        )
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
      const next = await client.selectCandidate(project.project_id, selectionResult, selectionCandidate.candidate_id)
      setSelection(next)
      setVerificationOpen(true)
      setPreparationGuide(null)
      setPreparationError('')
      setActionStatus(`${selectionCandidate.display_name}의 실제 조건 검증을 시작했어요.`)
      window.scrollTo({ top: 0 })
    } catch (error) {
      setActionStatus(userError(error, '후보를 선택하지 못했습니다.'))
    } finally {
      setSelectionBusy(false)
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
    const terminal = await waitForWorkflow(client, project.project_id, application.recompute_workflow, (progress) =>
      setActionStatus(`점포 조건 재계산 ${progress.completed_stage_count}/${progress.total_stage_count}`),
    )
    if (terminal.status !== 'SUCCEEDED') throw new Error('점포 조건 재계산을 완료하지 못했습니다.')
    const nextResult = await client.getResult(project.project_id)
    if (nextResult.freshness !== 'CURRENT') throw new Error('점포 조건 반영 결과를 최신 상태로 저장하지 못했습니다.')
    const source = candidateSource(activeCandidate)
    const nextCandidate = nextResult.candidates.find((candidate) => candidateSource(candidate) === source)
    if (!nextCandidate) throw new Error('재계산된 후보를 찾지 못했습니다.')
    setResult(nextResult)
    setActiveCandidateId(nextCandidate.candidate_id)
    setSelection((current) => current ? { ...current, selected_state_version: application.applied_state_version } : current)
    setActionStatus('실제 점포 조건을 반영해 판단을 다시 계산했습니다.')
    return { mode: 'LIVE', application, candidate: nextCandidate, result: nextResult }
  }

  const updateResult = (next: ResultView) => {
    setResult(next)
    setActiveCandidateId(next.primary_candidate_id ?? next.candidates[0]?.candidate_id ?? '')
    setSelection(null)
    setVerificationOpen(false)
    setPreparationGuide(null)
  }

  const refreshAfterDocument = async () => {
    if (!activeCandidate) return
    const nextResult = await client.getResult(project.project_id)
    if (nextResult.freshness !== 'CURRENT') throw new Error('문서 반영 결과를 최신 상태로 저장하지 못했습니다.')
    const source = candidateSource(activeCandidate)
    const nextCandidate = nextResult.candidates.find((candidate) => candidateSource(candidate) === source) ?? nextResult.candidates[0]
    setResult(nextResult)
    setActiveCandidateId(nextCandidate?.candidate_id ?? '')
    setSelection((current) => current ? { ...current, selected_state_version: nextResult.current_head.state_version } : current)
    setActionStatus(
      nextCandidate?.review_status === 'EXCLUDED'
        ? '문서에서 확인한 값이 판단을 바꿨어요. 새 판정 이유를 확인해 주세요.'
        : '문서에서 확인한 값으로 창업안을 다시 계산했습니다.',
    )
  }

  const openConditionChange = (suggestion?: string) => {
    if (suggestion) setFeedbackSuggestion(suggestion)
    document.getElementById('conditionModeButton')?.click()
    window.setTimeout(() => {
      document.getElementById('resultFeedback')?.scrollIntoView?.({ block: 'start' })
      document.getElementById('feedbackInput')?.focus()
    }, 0)
  }

  if (!activeCandidate) {
    return <main className="analysis-stage"><h1>분석할 창업안을 만들지 못했어요</h1><p>희망 지역과 자금 조건을 확인한 뒤 다시 분석해 주세요.</p></main>
  }

  if (selection && verificationOpen) {
    return (
      <VerificationFlow
        client={client}
        projectId={project.project_id}
        candidate={activeCandidate}
        selection={selection}
        guide={preparationGuide}
        busy={preparationBusy}
        error={preparationError}
        onLoadProcedures={() => void loadPreparationGuide(selection)}
        onBack={() => { setVerificationOpen(false); window.scrollTo({ top: 0 }) }}
        onApply={applyPropertyTerms}
        onDocumentApplied={refreshAfterDocument}
      />
    )
  }

  return (
    <div className="shell min-h-dvh">
      <header className="topbar">
        <a className="wordmark" href="#top">CaffeMate</a>
        <div className="topbar__meta">
          <Badge tone={result.freshness === 'CURRENT' ? 'success' : 'warning'}>{internalLabel(result.freshness)}</Badge>
          <span className="version">결과 생성 {createdAt}</span>
        </div>
      </header>
      <main className="page result-page" id="top">
        {result.freshness === 'STALE' && (
          <div className="demo-notice" role="note"><span className="demo-notice__mark">!</span><p>입력 조건이 바뀌었어요. 실제 조건 검증을 시작하면 먼저 최신 조건으로 다시 계산합니다.</p></div>
        )}
        <ResultHero candidate={activeCandidate} />
        <CandidateComparison
          candidates={candidates}
          activeCandidateId={activeCandidate.candidate_id}
          onSelect={(candidateId) => { setActiveCandidateId(candidateId); window.scrollTo({ top: 0 }) }}
        />
        <DecisionReasons candidate={activeCandidate} />
        <FinanceBreakdown candidate={activeCandidate} />
        <MarketContext candidate={activeCandidate} />
        <RefinableUnknowns candidate={activeCandidate} />
        <ExternalChecks candidate={activeCandidate} />
        <Counterfactuals candidate={activeCandidate} />

        <section className="result-next-action" aria-labelledby="resultNextActionTitle">
          <div>
            <p className="result-kicker">다음 행동</p>
            <h2 id="resultNextActionTitle">이제 무엇을 확인할까요?</h2>
            <p>{actionStatus}</p>
          </div>
          <div className="action-group">
            {selection ? (
              <button className="btn btn--primary" type="button" onClick={() => { setVerificationOpen(true); window.scrollTo({ top: 0 }) }}>검증 계속하기</button>
            ) : activeCandidate.review_status === 'EXCLUDED' ? (
              <button className="btn btn--primary" type="button" onClick={() => openConditionChange('현재 조건에서 막힌 이유를 줄일 수 있도록 예산이나 운영 조건을 바꾸고 다시 비교하고 싶어요.')}>내 조건을 바꾸고 다시 비교하기</button>
            ) : (
              <button className="btn btn--primary" disabled={selectionBusy} type="button" onClick={select}>{selectionBusy ? '검증 준비 중' : '실제 조건으로 검증하기'}</button>
            )}
            {activeCandidate.review_status !== 'EXCLUDED' && (
              <button className="btn btn--accent" type="button" onClick={() => openConditionChange()}>내 조건을 바꾸고 다시 비교하기</button>
            )}
          </div>
        </section>

        <div className="result-assistant-wrap">
          <FeedbackPanel
            key={`${result.result_bundle_id}-${activeCandidate.candidate_id}`}
            client={client}
            projectId={project.project_id}
            result={result}
            candidate={activeCandidate}
            onResult={updateResult}
            suggestion={feedbackSuggestion}
          />
        </div>
      </main>
      <footer className="footer"><strong>CaffeMate</strong><span>계약과 최종 창업 결정을 대신하지 않습니다.</span><span>현재 입력과 확인된 자료를 기준으로 계산했어요.</span></footer>
    </div>
  )
}
