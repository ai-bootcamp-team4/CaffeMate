import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { McpConnector } from './router'

type CostReferenceType = 'MINIMUM_WAGE'

interface MinimumWageReferenceSnapshot {
  reference_id: string
  reference_type: 'MINIMUM_WAGE'
  effective_from: string
  effective_to: string
  hourly_rate_krw: number
  monthly_equivalent_hours: number
  monthly_equivalent_krw: number
  source_title: string
  source_ref: string
  source_anchor: string
  published_or_data_date: string
}

interface CostReferenceSnapshot {
  schema_version: '1.0.0'
  snapshot_id: string
  checked_at: string
  references: MinimumWageReferenceSnapshot[]
}

const SNAPSHOT_TEXT = readFileSync(
  resolve(process.cwd(), 'mcp/data/cost-references-20260825.json'),
  'utf8',
)
const SNAPSHOT = JSON.parse(SNAPSHOT_TEXT) as CostReferenceSnapshot
const SNAPSHOT_DIGEST = `sha256:${createHash('sha256').update(SNAPSHOT_TEXT).digest('hex')}`

function assertDate(value: string, label: string): void {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(`${value}T00:00:00Z`))) {
    throw new Error(`MCP_COST_REFERENCE_${label}_INVALID`)
  }
}

function validateSnapshot(snapshot: CostReferenceSnapshot): void {
  if (snapshot.schema_version !== '1.0.0' || !snapshot.snapshot_id || !snapshot.checked_at) {
    throw new Error('MCP_COST_REFERENCE_SNAPSHOT_INVALID')
  }
  const ids = new Set<string>()
  const byType = new Map<CostReferenceType, MinimumWageReferenceSnapshot[]>()
  for (const reference of snapshot.references) {
    if (ids.has(reference.reference_id)) {
      throw new Error(`MCP_COST_REFERENCE_DUPLICATE_ID:${reference.reference_id}`)
    }
    ids.add(reference.reference_id)
    assertDate(reference.effective_from, 'EFFECTIVE_FROM')
    assertDate(reference.effective_to, 'EFFECTIVE_TO')
    assertDate(reference.published_or_data_date, 'DATA_DATE')
    if (reference.effective_from > reference.effective_to) {
      throw new Error(`MCP_COST_REFERENCE_RANGE_INVALID:${reference.reference_id}`)
    }
    if (reference.hourly_rate_krw <= 0 || reference.monthly_equivalent_hours <= 0) {
      throw new Error(`MCP_COST_REFERENCE_VALUE_INVALID:${reference.reference_id}`)
    }
    if (reference.hourly_rate_krw * reference.monthly_equivalent_hours
      !== reference.monthly_equivalent_krw) {
      throw new Error(`MCP_COST_REFERENCE_MONTHLY_MISMATCH:${reference.reference_id}`)
    }
    if (!reference.source_ref.startsWith('https://') || !reference.source_anchor) {
      throw new Error(`MCP_COST_REFERENCE_SOURCE_INVALID:${reference.reference_id}`)
    }
    const rows = byType.get(reference.reference_type) ?? []
    rows.push(reference)
    byType.set(reference.reference_type, rows)
  }
  for (const rows of byType.values()) {
    const ordered = [...rows].sort((a, b) => a.effective_from.localeCompare(b.effective_from))
    for (let index = 1; index < ordered.length; index += 1) {
      if (ordered[index - 1].effective_to >= ordered[index].effective_from) {
        throw new Error('MCP_COST_REFERENCE_EFFECTIVE_RANGE_OVERLAP')
      }
    }
  }
}

validateSnapshot(SNAPSHOT)

function evidenceId(reference: MinimumWageReferenceSnapshot): string {
  return `cost-reference:${reference.reference_id}`
}

function evidence(
  reference: MinimumWageReferenceSnapshot,
  projectId: string,
  observedAt: string,
) {
  return {
    schema_version: '2.0.0',
    evidence_id: evidenceId(reference),
    project_id: projectId,
    claim_type: 'LABOR_COST_REFERENCE',
    metric: 'MINIMUM_WAGE_MONTHLY_209H',
    value: { kind: 'INTEGER', value: reference.monthly_equivalent_krw },
    value_kind: 'EVIDENCED_FACT',
    unit: 'KRW/month',
    geographic_scope: {
      scope_type: 'NATIONAL',
      scope_id: 'KR',
      boundary_version: null,
    },
    source: {
      title: reference.source_title,
      source_ref: reference.source_ref,
      authority: 'PRIMARY_OFFICIAL',
      source_type: 'WEB',
      source_family: 'LABOR_COST_REFERENCE',
      published_or_data_date: reference.published_or_data_date,
      source_observed_at: observedAt,
      document_version: reference.effective_from,
      checksum: SNAPSHOT_DIGEST,
    },
    original_anchor: {
      anchor_type: 'SECTION',
      locator: reference.source_anchor,
      excerpt_hash: `sha256:${createHash('sha256').update(reference.source_anchor).digest('hex')}`,
    },
    freshness_status: 'FRESH',
    conflict_status: 'NONE',
    retrieved_at: observedAt,
    missing_context: [
      'PAID_STAFF_FTE_IS_REGISTERED_ASSUMPTION',
      'EXCLUDES_OVERTIME_ALLOWANCES_AND_EMPLOYER_INSURANCE',
    ],
    durable_evidence_refs: [reference.source_ref],
  }
}

export function createCostReferenceConnector(options: { now?: () => Date } = {}): McpConnector {
  const clock = options.now ?? (() => new Date())
  return async (rawInput, scope) => {
    const input = rawInput as {
      reference_types: CostReferenceType[]
      as_of: string
    }
    assertDate(input.as_of, 'AS_OF')
    const requested = new Set(input.reference_types)
    const selected = SNAPSHOT.references.filter((reference) => (
      requested.has(reference.reference_type)
      && reference.effective_from <= input.as_of
      && input.as_of <= reference.effective_to
    ))
    const observedAt = clock().toISOString()
    return {
      schema_version: '1.0.0',
      request_id: scope.requestId,
      tool_name: 'get_cost_reference',
      tool_version: '1.0.0',
      status: selected.length ? 'OK' : 'NOT_FOUND',
      project_id: scope.ventureProjectId,
      evidence_records: selected.map((reference) => evidence(
        reference,
        scope.ventureProjectId,
        observedAt,
      )),
      missing_fields: selected.length ? [] : input.reference_types.map(
        (referenceType) => `cost_reference:${referenceType}`,
      ),
      conflicts: [],
      source_trace: selected.map((reference) => ({
        source_id: reference.reference_id,
        source_ref: reference.source_ref,
        data_date: reference.published_or_data_date,
        retrieved_at: observedAt,
        content_digest: SNAPSHOT_DIGEST,
      })),
      error_codes: [],
      observed_at: observedAt,
      data: selected.map((reference) => ({
        reference_type: reference.reference_type,
        effective_from: reference.effective_from,
        effective_to: reference.effective_to,
        hourly_rate_krw: reference.hourly_rate_krw,
        monthly_equivalent_hours: reference.monthly_equivalent_hours,
        monthly_equivalent_krw: reference.monthly_equivalent_krw,
        evidence_id: evidenceId(reference),
      })),
    }
  }
}