import type { ResultCandidate } from '../apiClient'
import { Badge } from '../ui/Badge'
import { conclusionCopy, publicStatus } from './resultPresentation'

export function ResultHero({ candidate }: { candidate: ResultCandidate }) {
  return (
    <section className="result-hero" aria-labelledby="resultConclusionTitle">
      <div className="result-hero__eyebrow">
        <Badge>{candidate.case_type === 'FRANCHISE' ? '프랜차이즈' : '개인카페'}</Badge>
        <Badge tone={candidate.review_status === 'REVIEW_RECOMMENDED' ? 'success' : candidate.review_status === 'CONDITIONAL_REVIEW' ? 'warning' : ''}>
          {publicStatus(candidate.review_status)}
        </Badge>
      </div>
      <p className="result-kicker">이번 분석의 결론</p>
      <h1 id="resultConclusionTitle">이번 분석의 결론</h1>
      <h2 className="result-hero__candidate">{candidate.display_name}</h2>
      <p className="result-hero__lede">{conclusionCopy(candidate)}</p>
      <p className="result-hero__summary">{candidate.summary}</p>
    </section>
  )
}
