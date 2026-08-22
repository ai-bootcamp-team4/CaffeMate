import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const deploy = readFileSync(join(process.cwd(), 'scripts/deploy-private-mcp.sh'), 'utf8')
const verify = readFileSync(join(process.cwd(), 'scripts/verify-private-mcp.sh'), 'utf8')
const dockerfile = readFileSync(join(process.cwd(), 'deploy/mcp.Dockerfile'), 'utf8')

describe('private MCP deployment IAM contract', () => {
  it('uses the minimum retrieval role and verifies effective mutation denial', () => {
    expect(deploy).toContain('caffemateMcpRetriever')
    expect(deploy).toContain('aiplatform.ragCorpora.query,discoveryengine.rankingConfigs.rank')
    expect(deploy).toContain("remove_project_role_binding \"serviceAccount:${runtime_sa}\" 'roles/aiplatform.user'")
    expect(deploy).toContain("remove_project_role_binding \"serviceAccount:${runtime_sa}\" 'roles/discoveryengine.viewer'")
    expect(verify).toContain('caffemateMcpRetriever')
    expect(deploy).toContain('_SOURCE_REVISION=${source_revision}')
    expect(verify).toContain('verified_build_id_for_image')
    expect(verify).toContain('Policy Troubleshooter confirms MCP has no prohibited effective mutation permission')
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

  it('pins the base image and carries only source needed by MCP runtime plus the standard Agent release preflight', () => {
    expect(dockerfile).toMatch(/^FROM node:24-slim@sha256:[0-9a-f]{64}$/m)
    expect(dockerfile).toContain('COPY --chown=node:node docs/contracts ./docs/contracts')
    expect(dockerfile).toContain('COPY --chown=node:node agents/release-manifest.json ./agents/release-manifest.json')
    expect(dockerfile).toContain('COPY --chown=node:node agents/src ./agents/src')
    expect(dockerfile).toContain('COPY --chown=node:node rag/src ./rag/src')
    expect(dockerfile).toContain('COPY --chown=node:node mcp/src ./mcp/src')
  })
})
