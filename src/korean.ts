export type KoreanParticle = '을/를' | '은/는' | '이/가' | '과/와' | '으로/로'

const HANGUL_SYLLABLE_START = 0xac00
const HANGUL_SYLLABLE_END = 0xd7a3
const JONGSEONG_COUNT = 28
const RIEUL_JONGSEONG_INDEX = 8

function finalJongseongIndex(value: string) {
  const finalCharacter = Array.from(value.trim()).at(-1)
  if (!finalCharacter) return 0

  const codePoint = finalCharacter.codePointAt(0)
  if (codePoint === undefined || codePoint < HANGUL_SYLLABLE_START || codePoint > HANGUL_SYLLABLE_END) {
    return 0
  }
  return (codePoint - HANGUL_SYLLABLE_START) % JONGSEONG_COUNT
}

export function withParticle(value: string, pair: KoreanParticle) {
  const jongseongIndex = finalJongseongIndex(value)
  const hasJongseong = jongseongIndex !== 0
  const particle = pair === '을/를'
    ? hasJongseong ? '을' : '를'
    : pair === '은/는'
      ? hasJongseong ? '은' : '는'
      : pair === '이/가'
        ? hasJongseong ? '이' : '가'
        : pair === '과/와'
          ? hasJongseong ? '과' : '와'
          : hasJongseong && jongseongIndex !== RIEUL_JONGSEONG_INDEX ? '으로' : '로'

  return `${value}${particle}`
}
