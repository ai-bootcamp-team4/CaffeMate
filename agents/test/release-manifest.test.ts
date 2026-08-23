import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { OFFICIAL_RAG_SOURCE } from '../../mcp/src/official-rag'
import { RAG_RANKER } from '../../rag/src/config'
import fixtureMatrix from '../fixtures/task-matrix.json'
import releaseManifest from '../release-manifest.json'
import {
  computeAgentContractBundleDigest,
  computePromptBundleDigest,
  verifyReleaseSourceSeal,
  type AgentReleaseManifest,
} from '../src/release-seal'
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
    expect(releaseManifest.runtime).toEqual({
      resource_name: expect.stringMatching(
        /^projects\/proj-aj20-211200020328\/locations\/asia-northeast3\/reasoningEngines\/[0-9]+$/,
      ),
      source_revision: expect.stringMatching(/^[0-9a-f]{40}$/),
      image_uri: expect.stringMatching(
        /^asia-northeast3-docker\.pkg\.dev\/proj-aj20-211200020328\/caffemate-agents\/caffemate-agent-runtime@sha256:[0-9a-f]{64}$/,
      ),
    })
    expect(releaseManifest.allow_global_fallback).toBe(false)
    expect(releaseManifest.network_mode).toBe('GCP_CONNECTED')
    expect('gcp_preflight_status' in releaseManifest).toBe(false)
    expect(releaseManifest.mcp).toEqual({
      protocol_revision: '2026-07-28',
      server_sdk: '@modelcontextprotocol/server@2.0.0',
      control_api_client_sdk: 'mcp==2.0.0',
      conformance_client_sdk: '@modelcontextprotocol/client@2.0.0',
      legacy_mode: 'reject',
      runtime: {
        service_name: 'caffemate-mcp',
        region: 'asia-northeast3',
        source_revision: '0158386ff462883e193ef3c26dcf43f7cb3cb768',
        image_uri: 'asia-northeast3-docker.pkg.dev/proj-aj20-211200020328/caffemate-backend/mcp@sha256:275141d9bbc9653ab715644b6b807539fc0e85ca1483ed42dc5eb36bb4ac7e82',
      },
    })
    const checkedInMcpManifestDigest = readFileSync('docs/contracts/mcp-tool-manifest.sha256', 'utf8').split(/\s+/)[0]
    expect(releaseManifest.mcp_manifest_digest).toBe(`sha256:${checkedInMcpManifestDigest}`)
    expect(releaseManifest.prompt_bundle_digest).toBe(computePromptBundleDigest())
    expect(releaseManifest.agent_contract_bundle_digest).toBe(computeAgentContractBundleDigest())

    expect(releaseManifest.index_generation).toEqual({
      generation_id: 'official-2026-08-22-v1',
      status: 'ACTIVE',
      corpus_resource_name: 'projects/proj-aj20-211200020328/locations/asia-northeast3/ragCorpora/5148740273991319552',
      parser_revision: 'vertex-layout-parser.v1',
      schema_version: 'caffemate.rag-index.v1',
      embedding_model_id: 'text-multilingual-embedding-002',
      reranker_id: RAG_RANKER.id,
      source_revisions: [{
        document_revision_id: OFFICIAL_RAG_SOURCE.documentRevisionId,
        source_family: OFFICIAL_RAG_SOURCE.sourceFamily,
        source_date: OFFICIAL_RAG_SOURCE.sourceDate,
        source_uri: OFFICIAL_RAG_SOURCE.sourceUri,
        gcs_object_generation: OFFICIAL_RAG_SOURCE.gcsGeneration,
        rag_file_resource_name: `projects/proj-aj20-211200020328/locations/asia-northeast3/ragCorpora/5148740273991319552/ragFiles/${OFFICIAL_RAG_SOURCE.ragFileId}`,
        content_digest: OFFICIAL_RAG_SOURCE.contentDigest,
      }],
      sealed_evaluation_digest: expect.stringMatching(/^sha256:[0-9a-f]{64}$/),
    })
    const sealedEvaluationInputDigest = `sha256:${createHash('sha256')
      .update(readFileSync('docs/evaluation/high-value-cases.yaml'))
      .digest('hex')}`
    expect(releaseManifest.index_generation.sealed_evaluation_digest).toBe(sealedEvaluationInputDigest)

    for (const [taskType, registration] of Object.entries(TASK_REGISTRY)) {
      expect(releaseManifest.tasks[taskType as keyof typeof releaseManifest.tasks]).toEqual({
        agent_name: registration.agentName,
        prompt_version: registration.promptVersion,
        input_schema_id: registration.inputSchemaId,
        output_schema_id: registration.outputSchemaId,
        deadline_seconds: registration.deadlineSeconds,
        thinking_level: registration.thinkingLevel,
        max_output_tokens: registration.maxOutputTokens,
      })
    }

    for (const fixture of fixtureMatrix.cases.filter((item) => item.task.task_type === 'EVIDENCE_PLAN')) {
      expect(fixture.task.tool_manifest_digest, fixture.id).toBe(releaseManifest.mcp_manifest_digest)
    }

    expect(verifyReleaseSourceSeal(releaseManifest as AgentReleaseManifest)).toEqual({ ok: true, issues: [] })
  })

  it('fails the release seal when prompt or payload-schema contents drift under stable symbolic ids', () => {
    const stalePromptDigest = structuredClone(releaseManifest) as AgentReleaseManifest
    stalePromptDigest.prompt_bundle_digest = `sha256:${'0'.repeat(64)}`
    expect(verifyReleaseSourceSeal(stalePromptDigest)).toMatchObject({
      ok: false,
      issues: [expect.objectContaining({ code: 'RELEASE_PROMPT_BUNDLE_MISMATCH' })],
    })

    const staleSchemaDigest = structuredClone(releaseManifest) as AgentReleaseManifest
    staleSchemaDigest.agent_contract_bundle_digest = `sha256:${'0'.repeat(64)}`
    expect(verifyReleaseSourceSeal(staleSchemaDigest)).toMatchObject({
      ok: false,
      issues: [expect.objectContaining({ code: 'RELEASE_AGENT_CONTRACT_BUNDLE_MISMATCH' })],
    })
  })

  it('fails the release seal when the MCP tool manifest digest differs from the checked-in manifest', () => {
    const staleMcpDigest = structuredClone(releaseManifest) as AgentReleaseManifest
    staleMcpDigest.mcp_manifest_digest = `sha256:${'0'.repeat(64)}`

    expect(verifyReleaseSourceSeal(staleMcpDigest)).toMatchObject({
      ok: false,
      issues: [expect.objectContaining({ code: 'RELEASE_MCP_MANIFEST_MISMATCH' })],
    })
  })

  it('fails the release seal when the pinned IndexGeneration is not ACTIVE', () => {
    const shadow = structuredClone(releaseManifest) as AgentReleaseManifest
    shadow.index_generation.status = 'SHADOW'
    expect(verifyReleaseSourceSeal(shadow)).toMatchObject({
      ok: false,
      issues: [expect.objectContaining({ code: 'RELEASE_INDEX_GENERATION_NOT_ACTIVE' })],
    })
  })
})
