import { useState } from 'react'
import { PreparationProcedures } from '../PreparationProcedures'
import type { PreparationGuide } from '../apiClient'

export function StartupPreparation({
  guide,
  busy,
  error,
  onLoad,
}: {
  guide: PreparationGuide | null
  busy: boolean
  error: string
  onLoad: () => void
}) {
  const [open, setOpen] = useState(false)

  const openProcedures = () => {
    setOpen(true)
    if (!guide && !busy) onLoad()
  }

  return (
    <section id="result-preparation" className="result-section startup-preparation" aria-labelledby="startupPreparationTitle">
      <header className="result-section__head">
        <p className="result-kicker">실제 진행 단계</p>
        <h2 id="startupPreparationTitle">실제로 진행한다면</h2>
        <p>경제성 판단 뒤에 필요한 공식 절차와 CaffeMate가 대신할 수 없는 최종 행동을 분리해서 확인합니다.</p>
      </header>

      {!open ? (
        <button className="btn btn--primary" type="button" onClick={openProcedures}>창업 준비 절차 보기</button>
      ) : (
        <PreparationProcedures guide={guide} busy={busy} error={error} onRetry={onLoad} />
      )}

      <div className="startup-boundary" role="note">
        <strong>CaffeMate 검토 이후에는 사람이 직접 결정해요</strong>
        <p>자료를 정리하고 질문을 만드는 것까지 도울 수 있지만, 아래 행동은 자동으로 수행하지 않습니다.</p>
        <div className="startup-boundary__actions">
          <span>계약 체결·서명</span>
          <span>송금·결제</span>
          <span>대출 신청·실행</span>
          <span>정부 신고·등록 제출</span>
          <span>본사·임대인·중개인 연락</span>
          <span>법적 확정 판단</span>
          <span>고액 지출 승인</span>
          <span>최종 창업 Go / No-Go</span>
        </div>
      </div>
    </section>
  )
}
