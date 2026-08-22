import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const deploy = readFileSync(join(process.cwd(), 'scripts/deploy-private-mcp.sh'), 'utf8')
const verify = readFileSync(join(process.cwd(), 'scripts/verify-private-mcp.sh'), 'utf8')

describe('private MCP deployment IAM contract', () => {
  it('grants and verifies both Vertex RAG and ranking read permissions for the runtime identity', () => {
    expect(deploy).toContain("--role='roles/aiplatform.user'")
    expect(deploy).toContain("--role='roles/discoveryengine.viewer'")
    expect(verify).toContain('roles/aiplatform.user')
    expect(verify).toContain('roles/discoveryengine.viewer')
  })
})