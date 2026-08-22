import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const deploy = readFileSync(join(process.cwd(), 'scripts/deploy-private-mcp.sh'), 'utf8')
const verify = readFileSync(join(process.cwd(), 'scripts/verify-private-mcp.sh'), 'utf8')
const dockerfile = readFileSync(join(process.cwd(), 'deploy/mcp.Dockerfile'), 'utf8')
const cloudbuild = readFileSync(join(process.cwd(), 'cloudbuild.mcp-image.yaml'), 'utf8')
const standardVerify = readFileSync(join(process.cwd(), 'scripts/verify-api-worker-runtime.sh'), 'utf8')
const provenance = readFileSync(join(process.cwd(), 'scripts/build-provenance-helpers.sh'), 'utf8')
const agentControl = readFileSync(join(process.cwd(), 'agents/src/control.ts'), 'utf8')

describe('private MCP deployment IAM contract', () => {
  it('uses the minimum retrieval role and verifies effective mutation denial', () => {
    expect(deploy).toContain('caffemateMcpRetriever')
    expect(deploy).toContain('aiplatform.ragCorpora.get,aiplatform.ragCorpora.query,discoveryengine.rankingConfigs.rank')
    expect(deploy).toContain("remove_project_role_binding \"serviceAccount:${runtime_sa}\" 'roles/aiplatform.user'")
    expect(deploy).toContain("remove_project_role_binding \"serviceAccount:${runtime_sa}\" 'roles/discoveryengine.viewer'")
    expect(verify).toContain('caffemateMcpRetriever')
    expect(verify).toContain('aiplatform.ragCorpora.get')
    expect(deploy).toContain('_SOURCE_REVISION=${source_revision}')
    expect(verify).toContain('verified_build_id_for_image')
    expect(verify).toContain('MCP runtime identity has no prohibited effective mutation permission')
  })
})

describe('private MCP artifact boundary', () => {
  it('installs a dedicated MCP production package instead of the repository root package', () => {
    expect(existsSync(join(process.cwd(), 'mcp/package.json'))).toBe(true)
    expect(existsSync(join(process.cwd(), 'mcp/package-lock.json'))).toBe(true)
    expect(dockerfile).toContain('COPY --chown=node:node mcp/package.json mcp/package-lock.json ./')
    expect(dockerfile).not.toContain('COPY --chown=node:node package.json package-lock.json ./')
  })

  it('keeps the MCP production dependency closure limited to runtime and release-preflight responsibilities', () => {
    const packageJson = JSON.parse(readFileSync(join(process.cwd(), 'mcp/package.json'), 'utf8')) as {
      dependencies?: Record<string, string>
    }
    expect(Object.keys(packageJson.dependencies ?? {}).sort()).toEqual([
      '@modelcontextprotocol/client',
      '@modelcontextprotocol/server',
      'ajv',
      'ajv-formats',
      'google-auth-library',
      'tsx',
    ])
  })

  it('separates the non-self-referential MCP runtime from the Agent release-preflight artifact', () => {
    expect(dockerfile).toMatch(/^FROM node:24-slim@sha256:[0-9a-f]{64} AS base$/m)
    expect(dockerfile).toContain('FROM base AS runtime')
    expect(dockerfile).toContain('FROM base AS release-preflight')

    const runtimeSection = dockerfile.split('FROM base AS runtime')[1]?.split('FROM base AS release-preflight')[0] ?? ''
    const preflightSection = dockerfile.split('FROM base AS release-preflight')[1] ?? ''
    expect(runtimeSection).toContain('COPY --chown=node:node docs/contracts ./docs/contracts')
    expect(runtimeSection).toContain('COPY --chown=node:node rag/src ./rag/src')
    expect(runtimeSection).toContain('COPY --chown=node:node mcp/src ./mcp/src')
    expect(runtimeSection).toContain('COPY --chown=node:node deploy/runtime-iam-smoke.mjs ./deploy/runtime-iam-smoke.mjs')
    expect(runtimeSection).not.toContain('agents/')
    expect(preflightSection).toContain('COPY --chown=node:node agents/release-manifest.json ./agents/release-manifest.json')
    expect(preflightSection).toContain('COPY --chown=node:node agents/src ./agents/src')
  })

  it('builds both targets from one reviewed source and runs Agent preflight from the verifier artifact', () => {
    expect(cloudbuild).toContain('--target, runtime')
    expect(cloudbuild).toContain('--target, release-preflight')
    expect(cloudbuild).toContain('agent-release-preflight')
    expect(deploy).toContain('agent-release-preflight:${source_revision}')
    expect(deploy).toContain('preflight_build_id')
    expect(standardVerify).toContain('agent-release-preflight:${source_revision}')
    expect(standardVerify).toContain('--image="$agent_release_preflight_image"')
    expect(standardVerify).not.toContain('--image="$mcp_image" --service-account="$release_verifier_sa"')
    expect(agentControl).not.toContain('CAFFEMATE_AGENT_RUNTIME_RESOURCE_NAME')
    expect(agentControl).not.toContain('CAFFEMATE_AGENT_RUNTIME_IMAGE_URI')
    expect(standardVerify).not.toContain('CAFFEMATE_AGENT_RUNTIME_RESOURCE_NAME')
    expect(standardVerify).not.toContain('CAFFEMATE_AGENT_RUNTIME_IMAGE_URI')
    expect(provenance).toContain('/caffemate-backend/agent-release-preflight:')
    expect(provenance).toContain('build-agent-release-preflight-image')
    expect(provenance).toContain('release-preflight')
  })
})
