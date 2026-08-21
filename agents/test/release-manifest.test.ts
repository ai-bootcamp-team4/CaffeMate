import { describe, expect, it } from 'vitest'
import fixtureMatrix from '../fixtures/task-matrix.json'
import releaseManifest from '../release-manifest.json'
import { AGENT_MODEL, GCP_LOCATIONS, TASK_REGISTRY } from '../src/registry'

describe('local agent release manifest', () => {
  it('pins model, region, prompt and schema registry together', () => {
    expect(releaseManifest.model).toMatchObject({
      id: AGENT_MODEL.id,
      approval_status: AGENT_MODEL.approvalStatus,
      region: AGENT_MODEL.region,
      thinking_level: AGENT_MODEL.thinkingLevel,
    })
    expect(releaseManifest.runtime_region).toBe(GCP_LOCATIONS.runtime)
    expect(releaseManifest.allow_global_fallback).toBe(false)
    expect(releaseManifest.network_mode).toBe('GCP_CONNECTED')
    expect(releaseManifest.gcp_preflight_status).toBe('AGENT_RUNTIME_VERIFIED_RAG_RELEASE_BLOCKED')
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

    for (const fixture of fixtureMatrix.cases.filter((item) => item.task.task_type === 'EVIDENCE_PLAN')) {
      expect(fixture.task.tool_manifest_digest, fixture.id).toBe(releaseManifest.mcp_manifest_digest)
    }
  })
})
