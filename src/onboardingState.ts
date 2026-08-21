export type BorrowingIntent = 'YES' | 'NO' | 'UNDECIDED'
export type CafeTypePreference = 'OPEN_TO_BOTH' | 'INDEPENDENT_ONLY' | 'FRANCHISE_ONLY'
export type OperationMode = 'DIRECT_FULL_TIME' | 'DIRECT_PART_TIME' | 'EMPLOYEE_LED' | 'UNDECIDED'

export interface OnboardingValues {
  targetAreaInput: string
  ownFundsKrw: string
  borrowingIntent: BorrowingIntent | ''
  cafeTypePreference: CafeTypePreference | ''
  operationMode: OperationMode | ''
  desiredOpeningPeriod: string
  priorCafeExperience: string
}

export const initialOnboardingValues: OnboardingValues = {
  targetAreaInput: '',
  ownFundsKrw: '',
  borrowingIntent: '',
  cafeTypePreference: '',
  operationMode: '',
  desiredOpeningPeriod: '',
  priorCafeExperience: '',
}

export function formatKrw(value: string) {
  const amount = Number(value)
  if (!Number.isFinite(amount) || amount <= 0) return '입력 전'
  return `${new Intl.NumberFormat('ko-KR').format(amount)}원`
}

export function formatKoreanKrw(value: string) {
  const amount = Number(value)
  if (!Number.isFinite(amount) || amount <= 0) return '입력 전'

  const wholeWon = Math.floor(amount)
  if (wholeWon < 10_000) return `${new Intl.NumberFormat('ko-KR').format(wholeWon)}원`

  const totalManWon = Math.floor(wholeWon / 10_000)
  if (totalManWon < 10_000) return `${new Intl.NumberFormat('ko-KR').format(totalManWon)}만원`

  const eok = Math.floor(totalManWon / 10_000)
  const remainderManWon = totalManWon % 10_000
  return remainderManWon === 0
    ? `${new Intl.NumberFormat('ko-KR').format(eok)}억원`
    : `${new Intl.NumberFormat('ko-KR').format(eok)}억 ${new Intl.NumberFormat('ko-KR').format(remainderManWon)}만원`
}

export function canContinue(step: number, values: OnboardingValues) {
  if (step === 0) return values.targetAreaInput.trim().length > 0
  if (step === 1) return Number(values.ownFundsKrw) >= 0 && values.ownFundsKrw !== '' && values.borrowingIntent !== ''
  if (step === 2) return values.cafeTypePreference !== ''
  if (step === 3) return values.operationMode !== ''
  return true
}
