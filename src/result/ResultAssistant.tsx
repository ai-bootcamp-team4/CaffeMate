import { type FormEvent, useEffect, useRef, useState } from 'react'
import {
  waitForWorkflow,
  type ControlApiClient,
  type FeedbackPreview,
  type ResultCandidate,
  type ResultExplanation,
  type ResultView,
} from '../apiClient'
import { displayText, displayValue, explanationError, internalLabel, uniqueLabels, userError } from '../presentation'

const ASSISTANT_SAMPLES = [
  '왜 이 안을 먼저 보나요?',
  '예산에서 가장 위험한 부분은 뭐예요?',
  '예산을 1억으로 바꿔줘',
] as const

interface ExplanationTurn {
  question: string
  answer: ResultExplanation
}

export function FeedbackPanel({
  client,
  projectId,
  result,
  candidate,
  onResult,
}: {
  client: ControlApiClient
  projectId: string
  result: ResultView
  candidate: ResultCandidate
  onResult: (result: ResultView) => void
}) {
  const [draft, setDraft] = useState('')
  const [answers, setAnswers] = useState<ExplanationTurn[]>([])
  const [preview, setPreview] = useState<FeedbackPreview | null>(null)
  const [busyAction, setBusyAction] = useState<'submit' | 'cancel' | 'confirm' | null>(null)
  const [status, setStatus] = useState('결과를 물어보거나 바꾸고 싶은 조건을 자연어로 입력해 주세요.')
  const [statusTone, setStatusTone] = useState<'idle' | 'loading' | 'error' | 'success'>('idle')
  const [invalid, setInvalid] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const latestTurnRef = useRef<HTMLDivElement>(null)
  const busy = busyAction !== null

  useEffect(() => {
    if (answers.length === 0 && !preview) return
    latestTurnRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [answers.length, preview])

  const requestTurn = async (rawInput: string) => {
    if (busy) return
    const input = rawInput.trim()
    if (!input) {
      setInvalid(true)
      setStatus('궁금한 점이나 바꾸고 싶은 조건을 입력해 주세요.')
      setStatusTone('error')
      inputRef.current?.focus()
      return
    }

    setInvalid(false)
    setBusyAction('submit')
    setStatus('현재 결과를 확인하고 있어요.')
    setStatusTone('loading')
    try {
      const answer = await client.explainResult(projectId, result, input, candidate.candidate_id)
      if (answer.suggested_action === 'OPEN_CONDITION_CHANGE') {
        const nextPreview = await client.createFeedbackPreview(projectId, input)
        setPreview(nextPreview)
        setStatus(
          nextPreview.status === 'CLARIFICATION_REQUIRED'
            ? '조건을 더 구체적으로 알려 주세요. 아직 결과는 바뀌지 않았습니다.'
            : nextPreview.status === 'REVIEW_REQUIRED'
              ? '조건 변경안을 만들었습니다. 적용 전 내용을 확인해 주세요.'
              : `조건 변경안 상태: ${internalLabel(nextPreview.status)}`,
        )
        setStatusTone(nextPreview.status === 'REVIEW_REQUIRED' ? 'success' : 'idle')
      } else {
        setAnswers((current) => [...current, { question: input, answer }])
        setStatus('답변을 확인했어요. 현재 결과는 바뀌지 않았습니다.')
        setStatusTone('success')
      }
      setDraft('')
    } catch (error) {
      setStatus(explanationError(error))
      setStatusTone('error')
    } finally {
      setBusyAction(null)
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void requestTurn(draft)
  }

  const cancel = async () => {
    if (!preview) return
    const needsRevision = preview.status !== 'REVIEW_REQUIRED'
    setBusyAction('cancel')
    setStatus('변경안을 닫고 있습니다.')
    setStatusTone('loading')
    try {
      await client.cancelFeedback(projectId, preview.preview_id)
      setPreview(null)
      setStatus(
        needsRevision
          ? '입력을 수정할 수 있습니다. 현재 결과는 바뀌지 않았습니다.'
          : '변경안을 취소했습니다. 현재 결과는 바뀌지 않았습니다.',
      )
      setStatusTone('idle')
      window.setTimeout(() => inputRef.current?.focus(), 0)
    } catch (error) {
      setStatus(userError(error, '변경안을 취소하지 못했습니다.'))
      setStatusTone('error')
    } finally {
      setBusyAction(null)
    }
  }

  const confirm = async () => {
    if (!preview?.proposal_digest) return
    setBusyAction('confirm')
    setStatus('확인한 변경안을 반영하고 결과를 다시 계산하고 있습니다.')
    setStatusTone('loading')
    try {
      const resolution = await client.confirmFeedback(projectId, preview)
      if (resolution.workflow) {
        const progress = await waitForWorkflow(client, projectId, resolution.workflow, (next) =>
          setStatus(`결과 재계산 ${next.completed_stage_count}/${next.total_stage_count}`),
        )
        if (!['SUCCEEDED', 'PARTIAL'].includes(progress.status)) {
          throw new Error(`재계산이 완료되지 않았습니다: ${internalLabel(progress.status)}`)
        }
        onResult(await client.getResult(projectId))
      }
      setPreview(null)
      setStatus('확인한 변경안을 반영하고 결과를 갱신했습니다.')
      setStatusTone('success')
    } catch (error) {
      setStatus(userError(error, '변경안을 반영하지 못했습니다.'))
      setStatusTone('error')
    } finally {
      setBusyAction(null)
    }
  }

  const changedFields = preview
    ? Object.keys(preview.after_founder ?? {}).filter(
        (key) => JSON.stringify(preview.before_founder[key]) !== JSON.stringify(preview.after_founder?.[key]),
      )
    : []

  return (
    <section className="feedback-panel" aria-label="CaffeMate 채팅">
      {(answers.length > 0 || preview) && (
        <div className="feedback-thread" aria-label="CaffeMate 대화">
          {answers.map((entry, index) => (
            <div
              className="explanation-turn"
              key={`${entry.answer.explanation_id}-${index}`}
              ref={index === answers.length - 1 && !preview ? latestTurnRef : undefined}
            >
              <p className="chat-bubble chat-bubble--user">{entry.question}</p>
              <article className="explanation-answer">
                <strong>{displayText(entry.answer.conclusion)}</strong>
                {entry.answer.reasons.length > 0 && (
                  <ul className="explanation-list">
                    {entry.answer.reasons.map((reason) => <li key={reason}>{displayText(reason)}</li>)}
                  </ul>
                )}
                {entry.answer.evidence.length > 0 && (
                  <div className="explanation-evidence">
                    <span>확인한 근거</span>
                    {entry.answer.evidence.map((evidence) => (
                      <div key={evidence.evidence_id}>
                        <strong>{displayText(evidence.source_title ?? evidence.label)}</strong>
                        {evidence.value && <span>{displayText(evidence.value)}</span>}
                        {evidence.source_ref && (
                          <a
                            href={evidence.source_ref}
                            target="_blank"
                            rel="noreferrer"
                            aria-label={`${displayText(evidence.source_title ?? evidence.label)} 근거 원문 보기`}
                          >
                            근거 원문 보기
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {entry.answer.unknowns.length > 0 && (
                  <p className="explanation-caveat">아직 확인할 정보: {entry.answer.unknowns.map(displayText).join(' · ')}</p>
                )}
                {entry.answer.decision_change_conditions.length > 0 && (
                  <p className="explanation-caveat">판단이 달라질 조건: {entry.answer.decision_change_conditions.map(displayText).join(' · ')}</p>
                )}
              </article>
            </div>
          ))}

          {preview && (
            <div className="explanation-turn" ref={latestTurnRef}>
              <p className="chat-bubble chat-bubble--user">{preview.latest_user_input}</p>
              <section className="proposal" aria-labelledby="proposalTitle">
                <div className="proposal__head">
                  <h3 id="proposalTitle">적용 전 변경 확인</h3>
                  <p>아직 결과에는 반영되지 않았습니다.</p>
                </div>
                {changedFields.map((field) => (
                  <div className="diff-row" key={field}>
                    <span className="diff-label">{internalLabel(field, '창업 조건')}</span>
                    <div className="diff-values">
                      <span className="diff-old">{displayValue(preview.before_founder[field])}</span>
                      <span>→</span>
                      <span className="diff-new">{displayValue(preview.after_founder?.[field])}</span>
                    </div>
                  </div>
                ))}
                {preview.clarifying_questions.map((question) => <p key={question}>{displayText(question)}</p>)}
                {preview.risk_flags.length > 0 && <p>주의: {uniqueLabels(preview.risk_flags).join(' · ')}</p>}
                <div className="feedback-actions">
                  <button
                    className="btn"
                    disabled={busy}
                    aria-busy={busyAction === 'cancel' || undefined}
                    type="button"
                    onClick={cancel}
                  >
                    {busyAction === 'cancel'
                      ? '돌아가는 중'
                      : preview.status === 'REVIEW_REQUIRED' ? '변경안 취소' : '입력 다시 하기'}
                  </button>
                  <button
                    className="btn btn--primary"
                    disabled={busy || preview.status !== 'REVIEW_REQUIRED' || !preview.proposal_digest}
                    aria-busy={busyAction === 'confirm' || undefined}
                    type="button"
                    onClick={confirm}
                  >
                    {busyAction === 'confirm' ? '변경 적용 중' : '변경 적용'}
                  </button>
                </div>
              </section>
            </div>
          )}
        </div>
      )}

      {!preview && answers.length === 0 && (
        <div className="sample-row" aria-label="추천 질문">
          {ASSISTANT_SAMPLES.map((sample) => (
            <button
              className="sample-chip"
              disabled={busy}
              type="button"
              key={sample}
              onClick={() => void requestTurn(sample)}
            >
              {sample}
            </button>
          ))}
        </div>
      )}

      <form className="feedback-form" onSubmit={submit}>
        <div className="feedback-compose feedback-compose--dock" data-state={busyAction === 'submit' ? 'loading' : invalid ? 'error' : undefined}>
          <label className="sr-only" htmlFor="resultAssistantInput">CaffeMate에게 물어보기</label>
          <textarea
            id="resultAssistantInput"
            ref={inputRef}
            value={draft}
            rows={1}
            disabled={busy || preview !== null}
            aria-busy={busyAction === 'submit' || undefined}
            aria-invalid={invalid || undefined}
            aria-describedby="resultAssistantStatus"
            onChange={(event) => {
              setDraft(event.target.value)
              if (invalid) setInvalid(false)
            }}
            placeholder="결과를 물어보거나 조건을 바꿔 보세요"
          />
          <button className="btn btn--primary" disabled={busy || preview !== null} aria-busy={busyAction === 'submit' || undefined} type="submit">
            {busyAction === 'submit' ? '확인 중' : '보내기'}
          </button>
        </div>
      </form>
      <p
        className="feedback-status"
        id="resultAssistantStatus"
        data-tone={statusTone === 'idle' ? undefined : statusTone}
        aria-live="polite"
      >
        {status}
      </p>
    </section>
  )
}
