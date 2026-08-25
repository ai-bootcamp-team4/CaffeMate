interface ResultSectionNavProps {
  showCandidates: boolean
  showMarket: boolean
  showExternal: boolean
}

const baseItems = [
  { href: '#result-conclusion', label: '결론' },
  { href: '#result-decision', label: '판정 이유' },
  { href: '#result-finance', label: '비용·계산' },
] as const

export function ResultSectionNav({ showCandidates, showMarket, showExternal }: ResultSectionNavProps) {
  const items = [
    baseItems[0],
    ...(showCandidates ? [{ href: '#result-candidates', label: '후보 비교' } as const] : []),
    baseItems[1],
    baseItems[2],
    ...(showMarket ? [{ href: '#result-market', label: '참고 상권' } as const] : []),
    ...(showExternal ? [{ href: '#result-external', label: '외부 확인' } as const] : []),
    { href: '#result-preparation', label: '진행 절차' } as const,
  ]

  return (
    <nav className="result-section-nav" aria-label="결과 바로가기">
      <span className="result-section-nav__label">바로가기</span>
      <div className="result-section-nav__links">
        {items.map((item) => <a href={item.href} key={item.href}>{item.label}</a>)}
      </div>
    </nav>
  )
}
