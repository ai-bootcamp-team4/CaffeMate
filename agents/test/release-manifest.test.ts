import { describe, expect, it } from 'vitest'
import releaseManifest from '../release-manifest.json'
import { AGENT_MODEL, TASK_REGISTRY } from '../src/registry'

describe('local agent release manifest', () => {
  it('pins model, region, prompt and schema registry together', () => {
    expect(releaseManifest.model.id).toBe(AGENT_MODEL.id)
    expect(releaseManifest.runtime_region).toBe(AGENT_MODEL.region)
    expect(releaseManifest.allow_global_fallback).toBe(false)
    expect(releaseManifest.gcp_preflight_status).toBe('NOT_RUN')
    expect(releaseManifest.mcp).toEqual({
      protocol_revision: '2026-07-28',
      server_sdk: '@modelcontextprotocol/server@2.0.0',
      client_sdk: '@modelcontextprotocol/client@2.0.0',
      legacy_mode: 'reject',
    })

    for (const [taskType, registration] of Object.entries(TASK_REGISTRY)) {
      expect(releaseManifest.tasks[taskType as keyof typeof releaseManifest.tasks]).toEqual({
        agent_name: registration.agentName,
        prompt_version: registration.promptVersion,
        input_schema_id: registration.inputSchemaId,
        output_schema_id: registration.outputSchemaId,
      })
    }
  })
})
