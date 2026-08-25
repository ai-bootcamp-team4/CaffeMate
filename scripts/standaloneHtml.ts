export type AssetReader = (assetPath: string) => string

function localAssetPath(url: string) {
  if (/^(?:https?:)?\/\//.test(url) || url.startsWith('data:')) return null
  return url.replace(/^\.\//, '').replace(/^\//, '')
}

function attribute(tag: string, name: string) {
  return tag.match(new RegExp(`\\b${name}="([^"]+)"`))?.[1] ?? null
}

export function inlineBuiltAssets(html: string, readAsset: AssetReader) {
  const withStyles = html.replace(/<link\b[^>]*\brel="stylesheet"[^>]*>/g, (tag) => {
    const href = attribute(tag, 'href')
    const assetPath = href ? localAssetPath(href) : null
    if (!assetPath || !assetPath.startsWith('assets/')) return tag
    const css = readAsset(assetPath).replace(/<\/style/gi, '<\\/style')
    return `<style data-caffemate-inline="${assetPath}">${css}</style>`
  })

  return withStyles.replace(/<script\b[^>]*\btype="module"[^>]*><\/script>/g, (tag) => {
    const src = attribute(tag, 'src')
    const assetPath = src ? localAssetPath(src) : null
    if (!assetPath || !assetPath.startsWith('assets/')) return tag
    const javascript = readAsset(assetPath).replace(/<\/script/gi, '<\\/script')
    return `<script type="module" data-caffemate-inline="${assetPath}">${javascript}</script>`
  })
}
