#!/usr/bin/env -S npx tsx

import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { GoogleAuth } from 'google-auth-library'
import { hydrateAgentTaskResult, type AgentModelInvocation } from '../../agents/src/model-executor'
import { buildSystemInstruction, type RolePromptVersion } from '../../agents/src/prompts'
import { AGENT_MODEL } from '../../agents/src/registry'
import { validateAgentTaskResult } from '../../agents/src/schema-validator'
import { validateAgentSemantics } from '../../agents/src/semantic-validator'
import type { AgentSemanticResult, AgentTask } from '../../agents/src/types'
import { VertexAgentModelClient } from '../../agents/src/vertex-model-client'

const root = resolve(import.meta.dirname, '..', '..')
const options = new Map(process.argv.slice(2).flatMap((value, index, values) => value.startsWith('--') ? [[value, values[index + 1]]] : []))
const jsonPath = resolve(root, options.get('--json') ?? 'docs/evaluation/reports/prompt-version-comparison-latest.json')
const markdownPath = resolve(root, options.get('--markdown') ?? 'docs/evaluation/reports/prompt-version-comparison-latest.md')
const projectId = options.get('--project') ?? spawnSync('gcloud', ['config', 'get-value', 'project'], { encoding: 'utf8' }).stdout.trim()

const auth = new GoogleAuth({ scopes: ['https://www.googleapis.com/auth/cloud-platform'] })

async function accessToken(): Promise<string> {
  if (process.env.KFP_POD_NAME || process.env.AIP_JOB_NAME || process.env.GOOGLE_APPLICATION_CREDENTIALS) {
    const value = await auth.getAccessToken()
    if (!value) throw new Error('application default access token is unavailable')
    return value
  }
  const result = spawnSync('gcloud', ['auth', 'print-access-token'], { encoding: 'utf8' })
  if (result.status !== 0 || !result.stdout.trim()) throw new Error('gcloud access token is unavailable')
  return result.stdout.trim()
}

function hash(value: string): string {
  return `sha256:${createHash('sha256').update(value).digest('hex')}`
}

function metrics(semantic: AgentSemanticResult) {
  const payload = semantic.payload as Record<string, unknown> | null
  const proposals = Array.isArray(payload?.candidate_proposals) ? payload.candidate_proposals : []
  const first = proposals[0] as Record<string, unknown> | undefined
  const assessments = Array.isArray(first?.fit_assessments) ? first.fit_assessments : []
  const axes = assessments.flatMap((item) => typeof (item as Record<string, unknown>).axis === 'string' ? [(item as Record<string, string>).axis] : [])
  const basisCount = assessments.filter((item) => {
    const value = item as Record<string, unknown>
    return ['input_field_refs', 'claim_refs', 'evidence_refs', 'assumption_refs', 'missing_context']
      .some((key) => Array.isArray(value[key]) && (value[key] as unknown[]).length > 0)
  }).length
  return { candidate_count: proposals.length, fit_axis_count: new Set(axes).size, assessment_basis_count: basisCount }
}

async function runVersion(client: VertexAgentModelClient, task: AgentTask, version: RolePromptVersion) {
  const systemInstruction = buildSystemInstruction(version)
  const invocation: AgentModelInvocation = {
    model: AGENT_MODEL.id,
    region: AGENT_MODEL.region,
    thinkingLevel: 'low',
    maxOutputTokens: 4096,
    agentName: 'PROPOSAL_AGENT',
    taskType: 'PROPOSE_INDEPENDENT',
    outputSchemaId: task.output_schema_id,
    repairAttempt: 0,
    systemInstruction,
    task,
  }
  const startedAt = Date.now()
  try {
    const response = await client.generate(invocation)
    if (response.kind !== 'TEXT') throw new Error('SAFETY_BLOCKED')
    const semantic = JSON.parse(response.text) as AgentSemanticResult
    const hydrated = hydrateAgentTaskResult(task, semantic)
    const schema = validateAgentTaskResult(hydrated)
    const semantics = validateAgentSemantics(task, hydrated)
    return {
      prompt_version: version,
      prompt_hash: hash(systemInstruction),
      duration_ms: Date.now() - startedAt,
      transport_pass: true,
      schema_pass: schema.ok,
      semantic_pass: semantics.ok,
      semantic_issue_codes: semantics.ok ? [] : semantics.issues.map((issue) => issue.code),
      ...metrics(semantic),
    }
  } catch (error) {
    return {
      prompt_version: version,
      prompt_hash: hash(systemInstruction),
      duration_ms: Date.now() - startedAt,
      transport_pass: false,
      schema_pass: false,
      semantic_pass: false,
      semantic_issue_codes: [],
      candidate_count: 0,
      fit_axis_count: 0,
      assessment_basis_count: 0,
      error_class: error instanceof Error ? error.name : 'UnknownError',
    }
  }
}

if (!projectId) throw new Error('GCP project id is required')
const matrix = JSON.parse(readFileSync(resolve(root, 'agents/fixtures/task-matrix.json'), 'utf8')) as { cases: Array<{ id: string; task: AgentTask }> }
const fixture = matrix.cases.find((item) => item.id === 'propose_independent-complete')
if (!fixture) throw new Error('comparison fixture is missing')
const client = new VertexAgentModelClient({ projectId, region: AGENT_MODEL.region, accessToken })
const versions: RolePromptVersion[] = ['proposal-agent.v2', 'proposal-agent.v3']
const results = []
for (const version of versions) results.push(await runVersion(client, fixture.task, version))
const baseline = results[0]
const current = results[1]
const gitRevision = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).stdout.trim()
const report = {
  schema_version: '1.0.0',
  generated_at: new Date().toISOString(),
  git_revision: gitRevision,
  project_id: projectId,
  model: AGENT_MODEL.id,
  fixture_id: fixture.id,
  runs_per_version: 1,
  results,
  comparison: {
    baseline_prompt: baseline.prompt_version,
    current_prompt: current.prompt_version,
    fit_axis_delta: current.fit_axis_count - baseline.fit_axis_count,
    basis_delta: current.assessment_basis_count - baseline.assessment_basis_count,
    current_pass: current.transport_pass && current.schema_pass && current.semantic_pass && current.fit_axis_count === 5,
  },
  limitation: '단일 고정 fixture의 1회 비교이며 운영 사용자 품질이나 통계적 우월성을 뜻하지 않는다.',
}
const markdown = `# CaffeMate 프롬프트 버전 비교\n\n- 생성 시각: ${report.generated_at}\n- 모델: \`${report.model}\`\n- Fixture: \`${report.fixture_id}\`\n- Git revision: \`${report.git_revision}\`\n\n| 버전 | 전송 | Schema | 의미 검증 | 후보 | Fit 축 | 근거 연결 축 | 시간 |\n|---|---:|---:|---:|---:|---:|---:|---:|\n${results.map((item) => `| ${item.prompt_version} | ${item.transport_pass ? 'PASS' : 'FAIL'} | ${item.schema_pass ? 'PASS' : 'FAIL'} | ${item.semantic_pass ? 'PASS' : 'FAIL'} | ${item.candidate_count} | ${item.fit_axis_count}/5 | ${item.assessment_basis_count}/5 | ${item.duration_ms}ms |`).join('\n')}\n\n> ${report.limitation}\n`
mkdirSync(dirname(jsonPath), { recursive: true })
mkdirSync(dirname(markdownPath), { recursive: true })
writeFileSync(jsonPath, `${JSON.stringify(report, null, 2)}\n`)
writeFileSync(markdownPath, markdown)
console.log(JSON.stringify(report.comparison))
if (!report.comparison.current_pass) process.exitCode = 1
