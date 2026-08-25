import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { McpConnector } from './router'

type CostReferenceType = 'MINIMUM_WAGE' | 'EMPLOYER_SOCIAL_INSURANCE'
type EmployerInsuranceComponentName =
  | 'NATIONAL_PENSION'
  | 'HEALTH_LONG_TERM_CARE'
  | 'UNEMPLOYMENT_BENEFIT'
  | 'EMPLOYMENT_STABILIZATION_VOCATIONAL'

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

interface EmployerInsuranceComponentSnapshot {
  component: EmployerInsuranceComponentName
  employer_rate_ppm: number
  source_title: string
  source_ref: string
  source_anchor: string
  published_or_data_date: string
}

interface EmployerSocialInsuranceReferenceSnapshot {
  reference_id: string
  reference_type: 'EMPLOYER_SOCIAL_INSURANCE'
  effective_from: string
  effective_to: string
  workplace_employee_upper_bound: number
  components: EmployerInsuranceComponentSnapshot[]
  unsupported_components: string[]
  excluded_adjustments: string[]
}

type CostReferenceSnapshotRow =
  | MinimumWageReferenceSnapshot
  | EmployerSocialInsuranceReferenceSnapshot

interface CostReferenceSnapshot {
  schema_version: '1.0.0'
  snapshot_id: string
  checked_at: string
  references: CostReferenceSnapshotRow[]
}

const REQUIRED_EMPLOYER_COMPONENTS: EmployerInsuranceComponentName[] = [
  'NATIONAL_PENSION',
  'HEALTH_LONG_TERM_CARE',
  'UNEMPLOYMENT_BENEFIT',
  'EMPLOYMENT_STABILIZATION_VOCATIONAL',
]
const REQUIRED_UNSUPPORTED_COMPONENTS = [
  'WORKERS_COMPENSATION_INDUSTRY_RATE_REQUIRED',
]
const REQUIRED_EXCLUDED_ADJUSTMENTS = [
  'CONTRIBUTION_BASE_CAPS_AND_FLOORS_NOT_APPLIED',
  'EXEMPTIONS_NOT_APPLIED',
  'SUPPORT_PROGRAMS_NOT_APPLIED',
]

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

function validateMinimumWage(reference: MinimumWageReferenceSnapshot): void {
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
}

function validateEmployerSocialInsurance(
  reference: EmployerSocialInsuranceReferenceSnapshot,
): void {
  if (!Number.isInteger(reference.workplace_employee_upper_bound)
    || reference.workplace_employee_upper_bound < 1) {
    throw new Error(`MCP_COST_REFERENCE_WORKPLACE_SCOPE_INVALID:${reference.reference_id}`)
  }
  const names = reference.components.map((component) => component.component)
  if (reference.components.length !== REQUIRED_EMPLOYER_COMPONENTS.length
    || new Set(names).size !== names.length
    || REQUIRED_EMPLOYER_COMPONENTS.some((name) => !names.includes(name))) {
    throw new Error(`MCP_COST_REFERENCE_COMPONENTS_INVALID:${reference.reference_id}`)
  }
  for (const component of reference.components) {
    if (!Number.isInteger(component.employer_rate_ppm) || component.employer_rate_ppm <= 0) {
      throw new Error(`MCP_COST_REFERENCE_RATE_INVALID:${reference.reference_id}`)
    }
    assertDate(component.published_or_data_date, 'DATA_DATE')
    if (!component.source_ref.startsWith('https://') || !component.source_anchor) {
      throw new Error(`MCP_COST_REFERENCE_SOURCE_INVALID:${reference.reference_id}`)
    }
  }
  if (reference.unsupported_components.length !== REQUIRED_UNSUPPORTED_COMPONENTS.length
    || REQUIRED_UNSUPPORTED_COMPONENTS.some(
      (value) => !reference.unsupported_components.includes(value),
    )) {
    throw new Error(`MCP_COST_REFERENCE_UNSUPPORTED_COMPONENTS_INVALID:${reference.reference_id}`)
  }
  if (reference.excluded_adjustments.length !== REQUIRED_EXCLUDED_ADJUSTMENTS.length
    || REQUIRED_EXCLUDED_ADJUSTMENTS.some(
      (value) => !reference.excluded_adjustments.includes(value),
    )) {
    throw new Error(`MCP_COST_REFERENCE_EXCLUDED_ADJUSTMENTS_INVALID:${reference.reference_id}`)
  }
}

function validateSnapshot(snapshot: CostReferenceSnapshot): void {
  if (snapshot.schema_version !== '1.0.0' || !snapshot.snapshot_id || !snapshot.checked_at) {
    throw new Error('MCP_COST_REFERENCE_SNAPSHOT_INVALID')
  }
  const ids = new Set<string>()
  const byType = new Map<CostReferenceType, CostReferenceSnapshotRow[]>()
  for (const reference of snapshot.references) {
    if (ids.has(reference.reference_id)) {
      throw new Error(`MCP_COST_REFERENCE_DUPLICATE_ID:${reference.reference_id}`)
    }
    ids.add(reference.reference_id)
    assertDate(reference.effective_from, 'EFFECTIVE_FROM')
    assertDate(reference.effective_to, 'EFFECTIVE_TO')
    if (reference.effective_from > reference.effective_to) {
      throw new Error(`MCP_COST_REFERENCE_RANGE_INVALID:${reference.reference_id}`)
    }
    if (reference.reference_type === 'MINIMUM_WAGE') {
      validateMinimumWage(reference)
    } else {
      validateEmployerSocialInsurance(reference)
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

function evidenceId(referenceId: string, component?: string): string {
  return component
    ? `cost-reference:${referenceId}:${component.toLowerCase()}`
    : `cost-reference:${referenceId}`
}

function commonEvidence(
  projectId: string,
  observedAt: string,
  evidenceIdValue: string,
  metric: string,
  value: number,
  unit: string,
  source: {
    title: string
    source_ref: string
    source_anchor: string
    published_or_data_date: string
  },
  documentVersion: string,
  missingContext: string[],
) {
  return {
    schema_version: '2.0.0',
    evidence_id: evidenceIdValue,
    project_id: projectId,
    claim_type: 'LABOR_COST_REFERENCE',
    metric,
    value: { kind: 'INTEGER', value },
    value_kind: 'EVIDENCED_FACT',
    unit,
    geographic_scope: {
      scope_type: 'NATIONAL',
      scope_id: 'KR',
      boundary_version: null,
    },
    source: {
      title: source.title,
      source_ref: source.source_ref,
      authority: 'PRIMARY_OFFICIAL',
      source_type: 'WEB',
      source_family: 'LABOR_COST_REFERENCE',
      published_or_data_date: source.published_or_data_date,
      source_observed_at: observedAt,
      document_version: documentVersion,
      checksum: SNAPSHOT_DIGEST,
    },
    original_anchor: {
      anchor_type: 'SECTION',
      locator: source.source_anchor,
      excerpt_hash: `sha256:${createHash('sha256').update(source.source_anchor).digest('hex')}`,
    },
    freshness_status: 'FRESH',
    conflict_status: 'NONE',
    retrieved_at: observedAt,
    missing_context: missingContext,
    durable_evidence_refs: [source.source_ref],
  }
}

function evidenceRecords(
  reference: CostReferenceSnapshotRow,
  projectId: string,
  observedAt: string,
): Record<string, unknown>[] {
  if (reference.reference_type === 'MINIMUM_WAGE') {
    return [commonEvidence(
      projectId,
      observedAt,
      evidenceId(reference.reference_id),
      'MINIMUM_WAGE_MONTHLY_209H',
      reference.monthly_equivalent_krw,
      'KRW/month',
      {
        title: reference.source_title,
        source_ref: reference.source_ref,
        source_anchor: reference.source_anchor,
        published_or_data_date: reference.published_or_data_date,
      },
      reference.effective_from,
      [
        'PAID_STAFF_FTE_IS_REGISTERED_ASSUMPTION',
        'EXCLUDES_OVERTIME_ALLOWANCES_AND_EMPLOYER_INSURANCE',
      ],
    )]
  }
  return reference.components.map((component) => commonEvidence(
    projectId,
    observedAt,
    evidenceId(reference.reference_id, component.component),
    `EMPLOYER_SOCIAL_INSURANCE_${component.component}`,
    component.employer_rate_ppm,
    'ppm_of_payroll',
    {
      title: component.source_title,
      source_ref: component.source_ref,
      source_anchor: component.source_anchor,
      published_or_data_date: component.published_or_data_date,
    },
    reference.effective_from,
    [
      'APPLIES_TO_REGISTERED_PAID_STAFF_FTE_ONLY',
      'WORKERS_COMPENSATION_RATE_REQUIRES_INDUSTRY_CLASSIFICATION',
      'SUPPORT_PROGRAMS_CAPS_AND_EXEMPTIONS_NOT_APPLIED',
    ],
  ))
}

function dataRow(reference: CostReferenceSnapshotRow) {
  if (reference.reference_type === 'MINIMUM_WAGE') {
    return {
      reference_type: reference.reference_type,
      effective_from: reference.effective_from,
      effective_to: reference.effective_to,
      hourly_rate_krw: reference.hourly_rate_krw,
      monthly_equivalent_hours: reference.monthly_equivalent_hours,
      monthly_equivalent_krw: reference.monthly_equivalent_krw,
      evidence_id: evidenceId(reference.reference_id),
    }
  }
  return {
    reference_type: reference.reference_type,
    effective_from: reference.effective_from,
    effective_to: reference.effective_to,
    workplace_employee_upper_bound: reference.workplace_employee_upper_bound,
    components: reference.components.map((component) => ({
      component: component.component,
      employer_rate_ppm: component.employer_rate_ppm,
      evidence_id: evidenceId(reference.reference_id, component.component),
    })),
    unsupported_components: reference.unsupported_components,
    excluded_adjustments: reference.excluded_adjustments,
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
    const selectedTypes = new Set(selected.map((reference) => reference.reference_type))
    const missingTypes = [...requested].filter((referenceType) => !selectedTypes.has(referenceType))
    const observedAt = clock().toISOString()
    const status = selected.length === 0
      ? 'NOT_FOUND'
      : missingTypes.length > 0 ? 'PARTIAL' : 'OK'
    return {
      schema_version: '1.0.0',
      request_id: scope.requestId,
      tool_name: 'get_cost_reference',
      tool_version: '1.0.0',
      status,
      project_id: scope.ventureProjectId,
      evidence_records: selected.flatMap((reference) => evidenceRecords(
        reference,
        scope.ventureProjectId,
        observedAt,
      )),
      missing_fields: missingTypes.map((referenceType) => `cost_reference:${referenceType}`),
      conflicts: [],
      source_trace: selected.flatMap((reference) => {
        if (reference.reference_type === 'MINIMUM_WAGE') {
          return [{
            source_id: reference.reference_id,
            source_ref: reference.source_ref,
            data_date: reference.published_or_data_date,
            retrieved_at: observedAt,
            content_digest: SNAPSHOT_DIGEST,
          }]
        }
        return reference.components.map((component) => ({
          source_id: `${reference.reference_id}:${component.component}`,
          source_ref: component.source_ref,
          data_date: component.published_or_data_date,
          retrieved_at: observedAt,
          content_digest: SNAPSHOT_DIGEST,
        }))
      }),
      error_codes: [],
      observed_at: observedAt,
      data: selected.map(dataRow),
    }
  }
}
