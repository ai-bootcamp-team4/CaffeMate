import type { PreparationGuide, PreparationProcedure, ProcedureType } from './apiClient'

/* Hallmark · pre-emit critique: P5 H4 E4 S4 R5 V3 */

interface OfficialSourceTrace {
  source_ref?: unknown
}

interface ProcedureWithSources extends PreparationProcedure {
  source_trace?: OfficialSourceTrace[]
}

interface GuideWithSources extends PreparationGuide {
  source_trace?: OfficialSourceTrace[]
  procedures: ProcedureWithSources[]
}

export interface PreparationProceduresProps {
  guide: PreparationGuide | null
  busy: boolean
  error: string
  onRetry: () => void
}

const procedureOrder: ProcedureType[] = [
  'FACILITY_REQUIREMENTS',
  'HYGIENE_EDUCATION',
  'FOOD_SERVICE_REPORT',
  'BUSINESS_REGISTRATION',
  'SIGNAGE',
  'FIRE_SAFETY',
]

const procedureCopy: Record<ProcedureType, {
  label: string
  fallbackAction: string
  preparation: string
  caution: string
}> = {
  FACILITY_REQUIREMENTS: {
    label: '시설 기준 확인',
    fallbackAction: '카페 영업에 필요한 점포 시설 기준을 확인해요.',
    preparation: '점포 도면, 면적과 현재 시설 정보',
    caution: '공사 전에 관할 기관에 현재 점포가 기준을 충족하는지 확인하세요.',
  },
  HYGIENE_EDUCATION: {
    label: '위생교육',
    fallbackAction: '영업신고에 필요한 위생교육을 이수해요.',
    preparation: '신청자 정보와 교육 신청에 필요한 정보',
    caution: '교육 대상과 인정 기간은 신청 전에 최신 안내를 확인하세요.',
  },
  FOOD_SERVICE_REPORT: {
    label: '휴게음식점 영업신고',
    fallbackAction: '카페 영업을 위한 영업신고 절차를 확인해요.',
    preparation: '점포 정보와 관할 기관이 안내하는 신고 서류',
    caution: '시설 기준과 위생교육 확인이 먼저 필요할 수 있어요.',
  },
  BUSINESS_REGISTRATION: {
    label: '사업자등록',
    fallbackAction: '카페 사업자등록에 필요한 신청 절차를 확인해요.',
    preparation: '사업자 정보와 사업장 관련 서류',
    caution: '영업신고와 사업자등록의 순서는 점포 상황에 맞춰 확인하세요.',
  },
  SIGNAGE: {
    label: '간판 신고 확인',
    fallbackAction: '설치할 간판의 신고 대상 여부를 확인해요.',
    preparation: '간판 크기, 위치와 설치 계획',
    caution: '건물과 지역에 따라 적용 기준이 달라질 수 있어요.',
  },
  FIRE_SAFETY: {
    label: '소방 안전 확인',
    fallbackAction: '점포에 적용되는 소방 안전 요건을 확인해요.',
    preparation: '점포 면적, 층과 소방시설 현황',
    caution: '점포 구조와 규모에 따라 필요한 확인이 달라질 수 있어요.',
  },
}

const boilerplatePatterns = [
  /read frog/gi,
  /찾기쉬운\s*생활법령정보/gi,
  /법령정보를\s*제공합니다/gi,
  /자세히\s*보기/gi,
]

function cleanText(value: unknown): string {
  if (typeof value !== 'string') return ''
  let cleaned = value
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;|&#160;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;|&#34;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
  for (const pattern of boilerplatePatterns) cleaned = cleaned.replace(pattern, ' ')
  return cleaned.replace(/\s+/g, ' ').trim()
}

function concise(value: unknown, fallback: string): string {
  const cleaned = cleanText(value)
  if (!cleaned) return fallback
  const sentence = cleaned.split(/(?<=[.!?요다])\s+/u)[0] ?? cleaned
  return sentence.length > 110 ? `${sentence.slice(0, 107).trimEnd()}…` : sentence
}

function uniqueActions(procedure: ProcedureWithSources): string[] {
  const copy = procedureCopy[procedure.procedure_type]
  const seen = new Set<string>()
  const actions: string[] = []
  for (const step of [...procedure.steps].sort((left, right) => left.step_order - right.step_order)) {
    const action = concise(step.title, copy.fallbackAction)
    const key = action.replace(/[\s.,!?·:;()[\]-]/g, '').toLocaleLowerCase('ko-KR')
    if (!key || seen.has(key)) continue
    seen.add(key)
    actions.push(action)
    if (actions.length === 3) break
  }
  return actions.length ? actions : [copy.fallbackAction]
}

function uniqueAuthorities(procedure: ProcedureWithSources): string {
  const values = procedure.steps
    .map((step) => concise(step.authority, ''))
    .filter(Boolean)
  return [...new Set(values)].join(' · ') || '관할 기관 확인 필요'
}

function isOfficialUrl(value: unknown): value is string {
  if (typeof value !== 'string') return false
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:'
  } catch {
    return false
  }
}

function sourceUrls(procedure: ProcedureWithSources, guide: GuideWithSources): string[] {
  const local = procedure.source_trace ?? []
  const shared = guide.source_trace ?? []
  const localUrls = [...new Set(local.map((source) => source.source_ref).filter(isOfficialUrl))]
  if (localUrls.length) return localUrls
  return [...new Set(shared.map((source) => source.source_ref).filter(isOfficialUrl))]
}

function cautionFor(procedure: ProcedureWithSources): string {
  const copy = procedureCopy[procedure.procedure_type]
  if (procedure.conflicts.length) return '자료 내용이 서로 달라 관할 기관 확인이 필요해요.'
  if (procedure.status === 'STALE') return '기준일이 지난 자료일 수 있어 최신 안내를 다시 확인하세요.'
  if (procedure.status === 'PARTIAL' || procedure.missing_fields.length) {
    return `일부 정보가 부족해요. ${copy.caution}`
  }
  return copy.caution
}

export function PreparationProcedures({ guide, busy, error, onRetry }: PreparationProceduresProps) {
  const typedGuide = guide as GuideWithSources | null
  const procedures = [...(typedGuide?.procedures ?? [])]
    .filter((procedure) => procedure.steps.length > 0)
    .sort((left, right) => procedureOrder.indexOf(left.procedure_type) - procedureOrder.indexOf(right.procedure_type))

  return (
    <article className="surface" aria-labelledby="officialProceduresTitle">
      <div className="surface__head">
        <h2 id="officialProceduresTitle">공식 창업 절차</h2>
        <p>
          {typedGuide?.jurisdiction_display_name
            ? `${typedGuide.jurisdiction_display_name} 기준으로 먼저 할 일을 정리했어요.`
            : '선택 지역을 기준으로 먼저 할 일을 정리했어요.'}
        </p>
      </div>

      {busy && (
        <div className="preparation-loading" role="status">
          <span aria-hidden="true" />
          <p>공식 절차를 확인하고 있어요.</p>
        </div>
      )}

      {!busy && procedures.length > 0 && (
        <div className="procedure-list">
          {procedures.map((procedure) => {
            const copy = procedureCopy[procedure.procedure_type]
            const urls = sourceUrls(procedure, typedGuide as GuideWithSources)
            return (
              <section className="procedure-row" key={procedure.procedure_type} aria-labelledby={`procedure-${procedure.procedure_type}`}>
                <div className="procedure-row__head">
                  <h3 id={`procedure-${procedure.procedure_type}`}>{copy.label}</h3>
                  <span>{procedure.steps.some((step) => step.required) ? '필수 확인' : '해당 시 확인'}</span>
                </div>
                <ol>
                  <li>
                    <strong>해야 할 일</strong>
                    {uniqueActions(procedure).map((action) => <span key={action}>{action}</span>)}
                  </li>
                  <li><strong>준비물</strong><span>{copy.preparation}</span></li>
                  <li><strong>신청처</strong><span>{uniqueAuthorities(procedure)}</span></li>
                  <li><strong>주의사항</strong><span>{cautionFor(procedure)}</span></li>
                  <li>
                    <strong>공식 출처</strong>
                    <div className="official-document__source">
                      {urls.length
                        ? urls.map((url, index) => <a href={url} target="_blank" rel="noreferrer" key={url}>공식 원문 보기{urls.length > 1 ? ` ${index + 1}` : ''}</a>)
                        : <span>공식 원문 주소 확인 필요</span>}
                    </div>
                  </li>
                </ol>
              </section>
            )
          })}
        </div>
      )}

      {!busy && procedures.length === 0 && (
        <div className="procedure-unavailable">
          <strong>공식 절차 자료를 아직 연결하지 못했어요</strong>
          <p>점포 비용 재계산은 계속할 수 있고, 신청 전에는 관할 기관 안내를 직접 확인해 주세요.</p>
          {error && <button className="btn btn--accent" type="button" onClick={onRetry}>다시 확인</button>}
        </div>
      )}
    </article>
  )
}
