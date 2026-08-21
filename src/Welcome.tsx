export default function Welcome({ onStart }: { onStart: () => void }) {
  return (
    <div className="welcome-shell">
      <header className="welcome-nav">
        <strong className="wordmark">CaffeMate</strong>
        <span>카페 창업 의사결정 도구</span>
      </header>

      <main className="welcome-main">
        <section className="welcome-hero" aria-labelledby="welcomeTitle">
          <div className="welcome-copy">
            <h1 id="welcomeTitle" aria-label="카페 창업, 감이 아닌 데이터로 시작하세요."><span aria-hidden="true">카페 창업,</span><span aria-hidden="true">감이 아닌 데이터로</span><span aria-hidden="true">시작하세요.</span></h1>
            <p className="welcome-lede">
              희망 지역과 예산을 입력하면 상권부터 창업비용, 경쟁환경, 인허가, 지원사업까지
              AI 에이전트가 조사해 하나의 창업 리포트로 정리합니다.
            </p>
            <button className="welcome-cta" type="button" onClick={onStart}>
              내 카페 창업 분석 시작하기 <span aria-hidden="true">→</span>
            </button>
            <p className="welcome-note">계약이나 투자를 대신 결정하지 않고, 확인할 근거와 다음 행동을 정리합니다.</p>
          </div>

          <figure className="welcome-illustration" aria-label="아이스라떼와 크루아상, 메뉴판과 식물이 놓인 작은 카페 풍경">
            <svg viewBox="0 0 560 520" role="img" aria-hidden="true">
              <path className="cafe-art__ground" d="M52 448 C154 426 384 430 510 452" />
              <g className="cafe-art__shop">
                <path className="cafe-art__wall" d="M116 142 H456 V416 H116 Z" />
                <path className="cafe-art__roof" d="M92 142 L128 76 H442 L480 142 Z" />
                <path className="cafe-art__awning" d="M108 142 H466 L448 194 H126 Z" />
                <path className="cafe-art__stripe" d="M160 142 L154 194 M212 142 L210 194 M264 142 L266 194 M316 142 L322 194 M368 142 L378 194 M420 142 L434 194" />
                <rect className="cafe-art__window" x="148" y="220" width="176" height="138" rx="4" />
                <path className="cafe-art__window-line" d="M236 220 V358 M148 288 H324" />
                <rect className="cafe-art__door" x="356" y="216" width="70" height="200" rx="3" />
                <circle className="cafe-art__handle" cx="410" cy="316" r="5" />
              </g>
              <g className="cafe-art__menu" transform="rotate(-3 84 338)">
                <path d="M54 266 H116 L126 410 H42 Z" />
                <path d="M59 294 H111 M56 318 H113 M53 342 H116" />
              </g>
              <g className="cafe-art__table">
                <ellipse cx="260" cy="390" rx="112" ry="20" />
                <path d="M260 408 V460 M222 460 H298" />
              </g>
              <g className="cafe-art__latte" transform="rotate(2 232 342)">
                <path d="M202 286 H258 L252 376 Q230 386 208 376 Z" />
                <path d="M205 306 H256" />
                <path d="M214 306 Q230 326 248 306" />
                <path d="M236 286 L250 254" />
              </g>
              <g className="cafe-art__croissant" transform="rotate(8 330 358)">
                <path d="M278 370 Q292 316 328 326 Q368 314 384 362 Q362 348 348 372 Q326 352 308 376 Q292 358 278 370 Z" />
                <path d="M308 334 Q318 354 308 376 M330 326 Q338 348 348 372 M350 329 Q356 346 360 356" />
              </g>
              <g className="cafe-art__plant">
                <path d="M458 372 H504 L496 430 H466 Z" />
                <path d="M480 372 Q456 338 470 310 M480 372 Q510 340 496 302 M480 354 Q446 346 452 322 M484 344 Q516 330 514 310" />
                <ellipse cx="464" cy="308" rx="14" ry="25" transform="rotate(-28 464 308)" />
                <ellipse cx="500" cy="300" rx="14" ry="27" transform="rotate(25 500 300)" />
                <ellipse cx="448" cy="322" rx="13" ry="22" transform="rotate(-55 448 322)" />
                <ellipse cx="515" cy="309" rx="12" ry="21" transform="rotate(52 515 309)" />
              </g>
              <path className="cafe-art__steam" d="M188 116 Q174 96 190 78 M218 116 Q204 90 222 66" />
            </svg>
          </figure>
        </section>

        <section className="welcome-process" aria-label="분석 과정">
          <p><strong><b>01</b> 지역 입력</strong><span>희망하는 창업 지역을 알려주세요.</span></p>
          <p><strong><b>02</b> 조건 분석</strong><span>예산과 운영 조건을 함께 분석합니다.</span></p>
          <p><strong><b>03</b> 리포트 확인</strong><span>상권·비용·경쟁환경부터 창업 준비 정보까지 확인합니다.</span></p>
        </section>
      </main>
    </div>
  )
}
