import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const deploy = readFileSync(join(process.cwd(), 'scripts/deploy-private-mcp.sh'), 'utf8')
const verify = readFileSync(join(process.cwd(), 'scripts/verify-private-mcp.sh'), 'utf8')

describe('private MCP deployment IAM contract', () => {
  it('grants and verifies both Vertex RAG and ranking read permissions for the runtime identity', () => {
    expect(deploy).toContain('caffemateMcpRetriever')
    expect(deploy).toContain('aiplatform.ragCorpora.query,discoveryengine.rankingConfigs.rank')
    expect(deploy).toContain("remove_project_role_binding \"serviceAccount:${runtime_sa}\" 'roles/aiplatform.user'")
    expect(deploy).toContain("remove_project_role_binding \"serviceAccount:${runtime_sa}\" 'roles/discoveryengine.viewer'")
    expect(verify).toContain('caffemateMcpRetriever')
    expect(deploy).toContain('_SOURCE_REVISION=${source_revision}')
    expect(verify).toContain('verified_build_id_for_image')
    expect(verify).toContain('MCP runtime identity has no prohibited effective mutation permission')
  })
})
