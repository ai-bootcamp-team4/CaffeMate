import { describe, expect, it } from 'vitest'
import { inlineBuiltAssets } from './standaloneHtml'

describe('inlineBuiltAssets', () => {
  it('inlines local Vite CSS and module JS while keeping remote font links', () => {
    const source = `<!doctype html><html><head><link rel="stylesheet" href="https://cdn.example/font.css"><link rel="stylesheet" crossorigin href="./assets/app.css"></head><body><div id="root"></div><script type="module" crossorigin src="./assets/app.js"></script></body></html>`
    const assets = new Map([
      ['assets/app.css', 'body { color: black; }'],
      ['assets/app.js', 'document.body.dataset.ready = "true";'],
    ])

    const output = inlineBuiltAssets(source, (path) => {
      const value = assets.get(path)
      if (value == null) throw new Error(`missing ${path}`)
      return value
    })

    expect(output).toContain('https://cdn.example/font.css')
    expect(output).toContain('<style data-caffemate-inline="assets/app.css">body { color: black; }</style>')
    expect(output).toContain('<script type="module" data-caffemate-inline="assets/app.js">document.body.dataset.ready = "true";</script>')
    expect(output).not.toContain('href="./assets/app.css"')
    expect(output).not.toContain('src="./assets/app.js"')
  })
})
