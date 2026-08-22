import { getMcpToolDefinition } from './manifest'
import type { McpConnector } from './router'

export type McpSourceHealthStatus = 'HEALTHY' | 'DEGRADED' | 'UNAVAILABLE' | 'STALE'

export interface McpSourceTraceObservation {
  sourceRef: string
  dataDate: string | null
  contentDigest: string
}

export interface McpSourceHealthObservation {
  status: McpSourceHealthStatus
  lastSuccessAt: string | null
  dataDate: string | null
  sourceTrace?: McpSourceTraceObservation
}

export interface McpSourceHealthProbeContext {
  asOf: string
  observedAt: Date
}

export interface McpConfiguredSource {
  sourceId: string
  dataDate: string | null
  probeHealth: (context: McpSourceHealthProbeContext) => Promise<McpSourceHealthObservation>
}

function unavailable(dataDate: string | null): McpSourceHealthObservation {
  return { status: 'UNAVAILABLE', lastSuccessAt: null, dataDate }
}

export function createSourceHealthConnector(
  configuredSources: readonly McpConfiguredSource[],
  now: () => Date,
): McpConnector {
  const definition = getMcpToolDefinition('get_source_health')
  if (!definition) throw new Error('MCP_SOURCE_HEALTH_TOOL_DEFINITION_MISSING')
  const sources = new Map<string, McpConfiguredSource>()
  for (const source of configuredSources) {
    if (sources.has(source.sourceId)) {
      throw new Error(`MCP_SOURCE_REGISTRY_DUPLICATE: ${source.sourceId}`)
    }
    sources.set(source.sourceId, source)
  }

  return async (rawInput, scope) => {
    const input = rawInput as { source_ids: string[]; as_of: string }
    const observedAt = now()
    const observations = await Promise.all(input.source_ids.map(async (sourceId) => {
      const source = sources.get(sourceId)
      if (!source) return { sourceId, observation: unavailable(null) }
      try {
        return { sourceId, observation: await source.probeHealth({ asOf: input.as_of, observedAt }) }
      } catch {
        return { sourceId, observation: unavailable(source.dataDate) }
      }
    }))

    const data = observations.map(({ sourceId, observation }) => ({
      source_id: sourceId,
      status: observation.status,
      last_success_at: observation.lastSuccessAt,
      data_date: observation.dataDate,
    }))
    const sourceTrace = observations.flatMap(({ sourceId, observation }) => observation.sourceTrace ? [{
      source_id: sourceId,
      source_ref: observation.sourceTrace.sourceRef,
      data_date: observation.sourceTrace.dataDate,
      retrieved_at: observedAt.toISOString(),
      content_digest: observation.sourceTrace.contentDigest,
    }] : [])
    const fullyHealthy = data.length > 0 && data.every((row) => row.status === 'HEALTHY')

    return {
      schema_version: '1.0.0',
      request_id: scope.requestId,
      tool_name: 'get_source_health',
      tool_version: definition.version,
      status: fullyHealthy ? 'OK' : 'PARTIAL',
      project_id: scope.ventureProjectId,
      evidence_records: [],
      missing_fields: fullyHealthy ? [] : ['healthy_source'],
      conflicts: [],
      source_trace: sourceTrace,
      error_codes: fullyHealthy ? [] : ['SOURCE_DEGRADED'],
      observed_at: observedAt.toISOString(),
      data,
    }
  }
}
