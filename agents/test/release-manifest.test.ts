import { describe, expect, it } from 'vitest'
import releaseManifest from '../release-manifest.json'
import { AGENT_MODEL, TASK_REGISTRY } from '../src/registry'

describe('local agent release manifest', () => {
  it('pins model, region, prompt and schema registry together', () => {
    expect(releaseManifest.model).toMatchObject({
      id: AGENT_MODEL.id,
      approval_status: AGENT_MODEL.approvalStatus,
    })
    expect(releaseManifest.runtime_region).toBe(AGENT_MODEL.region)
    expect(releaseManifest.allow_global_fallback).toBe(false)
    expect(releaseManifest.gcp_preflight_status).toBe('NOT_RUN')
    expect(releaseManifest.mcp).toEqual({
      protocol_revision: '2026-07-28',
      server_sdk: '@modelcontextprotocol/server@2.0.0',
      control_api_client_sdk: 'mcp==2.0.0',
      conformance_client_sdk: '@modelcontextprotocol/client@2.0.0',
      legacy_mode: 'reject',
    })
    expect(releaseManifest.mcp_manifest_digest).toBe(
      'sha256:72ac3711d0b2500a90ef974bb7d6a11eaceff1c8108439624525f581202a184b',
    )

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
