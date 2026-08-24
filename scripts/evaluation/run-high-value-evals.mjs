#!/usr/bin/env node

import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..', '..')
const sourcePath = resolve(root, 'docs/evaluation/high-value-cases.yaml')
const mapPath = resolve(root, 'scripts/evaluation/high-value-eval-map.json')
const args = new Map(process.argv.slice(2).flatMap((value, index, values) => (
  value.startsWith('--') ? [[value, values[index + 1]]] : []
)))
const jsonPath = resolve(root, args.get('--json') ?? 'docs/evaluation/reports/high-value-eval-latest.json')
const markdownPath = resolve(root, args.get('--markdown') ?? 'docs/evaluation/reports/high-value-eval-latest.md')

function sha256(value) {
  return `sha256:${createHash('sha256').update(value).digest('hex')}`
}

function readCases(source) {
  const rows = [...source.matchAll(/^\s+- id: (EV-\d{3})\n\s+title: (.+)$/gm)]
  return rows.map((match) => ({ id: match[1], title: match[2].trim() }))
}

function runSuite(suiteId, suite) {
  const startedAt = Date.now()
  const [command, ...commandArgs] = suite.command
  const result = spawnSync(command, commandArgs, {
    cwd: root,
    encoding: 'utf8',
    env: { ...process.env, NO_COLOR: '1' },
  })
  return {
    suite_id: suiteId,
    description: suite.description,
    command: suite.command,
    passed: result.status === 0,
    exit_code: result.status,
    duration_ms: Date.now() - startedAt,
    output_tail: `${result.stdout ?? ''}\n${result.stderr ?? ''}`.trim().split('\n').slice(-12),
  }
}

const source = readFileSync(sourcePath, 'utf8')
const mappingSource = readFileSync(mapPath, 'utf8')
const mapping = JSON.parse(mappingSource)
const cases = readCases(source)
const sourceIds = new Set(cases.map((item) => item.id))
const mappedIds = new Set(Object.keys(mapping.cases))
const missing = [...sourceIds].filter((id) => !mappedIds.has(id))
const stale = [...mappedIds].filter((id) => !sourceIds.has(id))
if (cases.length !== 35 || missing.length || stale.length) {
  throw new Error(`evaluation map mismatch: cases=${cases.length} missing=${missing.join(',')} stale=${stale.join(',')}`)
}

const suiteResults = Object.fromEntries(Object.entries(mapping.suites).map(([id, suite]) => [id, runSuite(id, suite)]))
const caseResults = cases.map((item) => {
  const suiteId = mapping.cases[item.id]
  const suite = suiteResults[suiteId]
  if (!suite) throw new Error(`unknown suite ${suiteId} for ${item.id}`)
  return { ...item, suite_id: suiteId, passed: suite.passed }
})
const passed = caseResults.filter((item) => item.passed).length
const gitRevision = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).stdout.trim()
const report = {
  schema_version: '1.0.0',
  generated_at: new Date().toISOString(),
  git_revision: gitRevision,
  evaluation_source_digest: sha256(source),
  execution_map_digest: sha256(mappingSource),
  granularity: mapping.granularity,
  summary: {
    total_cases: caseResults.length,
    passed_cases: passed,
    failed_cases: caseResults.length - passed,
    pass_rate: passed / caseResults.length,
  },
  suites: Object.values(suiteResults),
  cases: caseResults,
  limitation: '각 사례는 연결된 자동화 suite의 통과 여부로 판정한다. suite 통과는 실제 사용자 연구나 운영 데이터 성능을 뜻하지 않는다.',
}

const markdown = `# CaffeMate 고가치 평가 실행 보고\n\n` +
  `- 생성 시각: ${report.generated_at}\n` +
  `- Git revision: \`${report.git_revision}\`\n` +
  `- 통과: **${passed}/${caseResults.length} (${(report.summary.pass_rate * 100).toFixed(1)}%)**\n` +
  `- 판정 단위: ${report.granularity}\n\n` +
  `> ${report.limitation}\n\n` +
  `## Suite 결과\n\n` +
  `| Suite | 결과 | 시간 | 설명 |\n|---|---:|---:|---|\n` +
  Object.values(suiteResults).map((suite) => `| ${suite.suite_id} | ${suite.passed ? 'PASS' : 'FAIL'} | ${suite.duration_ms}ms | ${suite.description} |`).join('\n') +
  `\n\n## 사례 결과\n\n| ID | 제목 | Suite | 결과 |\n|---|---|---|---:|\n` +
  caseResults.map((item) => `| ${item.id} | ${item.title} | ${item.suite_id} | ${item.passed ? 'PASS' : 'FAIL'} |`).join('\n') + '\n'

mkdirSync(dirname(jsonPath), { recursive: true })
mkdirSync(dirname(markdownPath), { recursive: true })
writeFileSync(jsonPath, `${JSON.stringify(report, null, 2)}\n`)
writeFileSync(markdownPath, markdown)
console.log(JSON.stringify(report.summary))
if (passed !== caseResults.length) process.exitCode = 1
