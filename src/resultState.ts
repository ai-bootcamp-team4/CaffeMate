export type PanelName = 'overview' | 'market' | 'franchise' | 'funds' | 'risks'
export type Tone = '' | 'error' | 'success'
export type CandidateAction = 'primary' | 'compare' | null

export interface DiffValue {
  before: string
  after: string
}
export interface FeedbackProposal {
  raw: string
  brand?: DiffValue
  size?: DiffValue
  impact: string
}

export interface ChatMessage {
  id: number
  role: 'system' | 'user'
  text: string
}

export interface ResultState {
  activePanel: PanelName
  actionStatus: string
  candidateBusy: CandidateAction
  candidateTone: Tone
  excluded: boolean
  toastVisible: boolean
  feedbackDraft: string
  feedbackPhase: 'editing' | 'review' | 'applying'
  feedbackStatus: string
  feedbackTone: Tone
  proposal: FeedbackProposal | null
  history: ChatMessage[]
  appliedConditions: string[]
}

export type ResultAction =
  | { type: 'panel.changed'; panel: PanelName }
  | { type: 'candidate.started'; action: Exclude<CandidateAction, null> }
  | { type: 'candidate.completed'; message: string }
  | { type: 'candidate.tone.cleared' }
  | { type: 'candidate.excluded' }
  | { type: 'candidate.exclude.undone' }
  | { type: 'toast.hidden' }
  | { type: 'feedback.draft.changed'; value: string }
  | { type: 'feedback.invalid' }
  | { type: 'feedback.proposed'; proposal: FeedbackProposal }
  | { type: 'feedback.proposal.cancelled' }
  | { type: 'feedback.apply.started' }
  | { type: 'feedback.apply.completed' }

export const initialResultState: ResultState = {
  activePanel: 'overview',
  actionStatus: '초기 결과가 완성되었습니다. 후보를 선택하거나 결과를 조정할 수 있습니다.',
  candidateBusy: null,
  candidateTone: '',
  excluded: false,
  toastVisible: false,
  feedbackDraft: '',
  feedbackPhase: 'editing',
  feedbackStatus: '입력 대기 중',
  feedbackTone: '',
  proposal: null,
  history: [
    {
      id: 1,
      role: 'system',
      text: '바꿀 조건을 말해 주세요. 먼저 변경 제안을 만듭니다.',
    },
  ],
  appliedConditions: [],
}

export function buildFeedbackProposal(raw: string): FeedbackProposal {
  const normalized = raw.trim()
  const wantsBrandChange = normalized.includes('저가') || normalized.includes('브랜드')
  const wantsSmallStore = normalized.includes('10평') || normalized.includes('규모') || normalized.includes('평 이하')

  return {
    raw: normalized,
    brand: wantsBrandChange
      ? {
          before: '저가 포함',
          after: normalized.includes('저가') ? '저가 브랜드 제외' : '입력한 브랜드 선호 반영',
        }
      : undefined,
    size: wantsSmallStore
      ? {
          before: '10–14평',
          after: normalized.includes('10평') ? '10평 이하' : '입력한 규모 조건 반영',
        }
      : undefined,
    impact: '후보 재정렬 · 비용 범위 재계산',
  }
}

export function resultReducer(state: ResultState, action: ResultAction): ResultState {
  switch (action.type) {
    case 'panel.changed':
      return { ...state, activePanel: action.panel }
    case 'candidate.started':
      return {
        ...state,
        candidateBusy: action.action,
        candidateTone: '',
        actionStatus: '선택을 반영하는 중입니다.',
      }
    case 'candidate.completed':
      return {
        ...state,
        candidateBusy: null,
        candidateTone: 'success',
        actionStatus: action.message,
      }
    case 'candidate.tone.cleared':
      return { ...state, candidateTone: '' }
    case 'candidate.excluded':
      return {
        ...state,
        excluded: true,
        toastVisible: true,
        candidateTone: 'error',
        actionStatus: '브랜드 A 소형점을 결과에서 제외했습니다.',
      }
    case 'candidate.exclude.undone':
      return {
        ...state,
        excluded: false,
        toastVisible: false,
        candidateTone: '',
        actionStatus: '제외를 되돌렸습니다. 후보가 다시 표시됩니다.',
      }
    case 'toast.hidden':
      return { ...state, toastVisible: false }
    case 'feedback.draft.changed':
      return {
        ...state,
        feedbackDraft: action.value,
        feedbackTone: '',
        feedbackStatus: action.value.trim() ? '변경안 생성 준비됨' : '입력 대기 중',
      }
    case 'feedback.invalid':
      return {
        ...state,
        feedbackTone: 'error',
        feedbackStatus: '입력 내용을 확인해 주세요.',
      }
    case 'feedback.proposed':
      return {
        ...state,
        proposal: action.proposal,
        feedbackPhase: 'review',
        feedbackTone: '',
        feedbackStatus: '변경 제안 확인 대기 중 · 결과 미반영',
      }
    case 'feedback.proposal.cancelled':
      return {
        ...state,
        proposal: null,
        feedbackPhase: 'editing',
        feedbackTone: '',
        feedbackStatus: '변경 제안을 취소했습니다. 결과는 바뀌지 않았습니다.',
      }
    case 'feedback.apply.started':
      return {
        ...state,
        feedbackPhase: 'applying',
        feedbackTone: '',
        feedbackStatus: '확인된 변경안을 결과에 적용하는 중',
      }
    case 'feedback.apply.completed': {
      if (!state.proposal) return state
      const conditions = [state.proposal.brand?.after, state.proposal.size?.after].filter(
        (value): value is string => Boolean(value),
      )
      const nextId = state.history.reduce((max, item) => Math.max(max, item.id), 0) + 1
      return {
        ...state,
        actionStatus: '확인한 자연어 피드백을 결과 조건에 적용했습니다.',
        feedbackDraft: '',
        feedbackPhase: 'editing',
        feedbackTone: 'success',
        feedbackStatus: '변경 적용 완료 · 확인 후 반영됨',
        proposal: null,
        appliedConditions: conditions,
        history: [
          ...state.history,
          { id: nextId, role: 'user', text: state.proposal.raw },
          {
            id: nextId + 1,
            role: 'system',
            text: '확인한 조건을 적용했습니다. 후보 재정렬과 비용 범위 재계산이 필요한 상태입니다.',
          },
        ],
      }
    }
    default:
      return state
  }
}
