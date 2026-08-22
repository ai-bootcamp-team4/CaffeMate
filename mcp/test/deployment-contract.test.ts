import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const deploy = readFileSync(join(process.cwd(), 'scripts/deploy-private-mcp.sh'), 'utf8')
const verify = readFileSync(join(process.cwd(), 'scripts/verify-private-mcp.sh'), 'utf8')
const dockerfile = readFileSync(join(process.cwd(), 'deploy/mcp.Dockerfile'), 'utf8')

function sourceFiles(root: string): string[] {
  return readdirSync(root).flatMap((entry) => {
    const path = join(root, entry)
    return statSync(path).isDirectory() ? sourceFiles(path) : path.endsWith('.ts') ? [path] : []
  })
}

describe('private MCP deployment IAM contract', () => {
  it('grants and verifies both Vertex RAG and ranking read permissions for the runtime identity', () => {
    expect(deploy).toContain("--role='roles/aiplatform.user'")
    expect(deploy).toContain("--role='roles/discoveryengine.viewer'")
    expect(verify).toContain('roles/aiplatform.user')
    expect(verify).toContain('roles/discoveryengine.viewer')
  })
})

describe('private MCP artifact boundary', () => {
  it('installs a dedicated MCP production package instead of the repository root package', () => {
    const packagePath = join(process.cwd(), 'mcp/package.json')
    const lockPath = join(process.cwd(), 'mcp/package-lock.json')

    expect(existsSync(packagePath)).toBe(true)
    expect(existsSync(lockPath)).toBe(true)
    expect(dockerfile).toContain('COPY --chown=node:node mcp/package.json mcp/package-lock.json ./')
    expect(dockerfile).not.toContain('COPY --chown=node:node package.json package-lock.json ./')
  })

  it('keeps the MCP production dependency closure limited to its runtime responsibilities', () => {
    const packagePath = join(process.cwd(), 'mcp/package.json')
    expect(existsSync(packagePath)).toBe(true)
    if (!existsSync(packagePath)) return

    const packageJson = JSON.parse(readFileSync(packagePath, 'utf8')) as { dependencies?: Record<string, string> }
    expect(Object.keys(packageJson.dependencies ?? {}).sort()).toEqual([
      '@modelcontextprotocol/client',
      '@modelcontextprotocol/server',
      'ajv',
      'ajv-formats',
      'google-auth-library',
      'tsx',
    ])
  })

  it('pins the base image and copies only MCP, RAG, and contract sources into the image', () => {
    expect(dockerfile).toMatch(/^FROM node:24-slim@sha256:[0-9a-f]{64}$/m)
    expect(dockerfile).toContain('COPY --chown=node:node docs/contracts ./docs/contracts')
    expect(dockerfile).toContain('COPY --chown=node:node rag/src ./rag/src')
    expect(dockerfile).toContain('COPY --chown=node:node mcp/src ./mcp/src')
    expect(dockerfile).not.toContain('agents/')
  })

  it('does not import Agent implementation files from MCP or RAG runtime sources', () => {
    const sources = [...sourceFiles(join(process.cwd(), 'mcp/src')), ...sourceFiles(join(process.cwd(), 'rag/src'))]
    for (const source of sources) {
      expect(readFileSync(source, 'utf8'), source).not.toMatch(/\.\.\/\.\.\/agents\//)
    }
  })
})
