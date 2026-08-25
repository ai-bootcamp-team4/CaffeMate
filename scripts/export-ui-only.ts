import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { inlineBuiltAssets } from './standaloneHtml'

const root = resolve(import.meta.dirname, '..')
const buildDir = resolve(root, 'dist-ui')
const builtHtmlPath = resolve(buildDir, 'ui-only.html')
const outputPath = resolve(buildDir, 'caffemate-ui-demo.html')

const builtHtml = readFileSync(builtHtmlPath, 'utf8')
const standaloneHtml = inlineBuiltAssets(builtHtml, (assetPath) => (
  readFileSync(resolve(buildDir, assetPath), 'utf8')
))

writeFileSync(outputPath, standaloneHtml)
console.log(outputPath)
