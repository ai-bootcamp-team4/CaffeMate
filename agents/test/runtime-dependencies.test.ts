import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import packageJson from '../../package.json'

describe('Agent and MCP runtime dependencies', () => {
  it('keeps runtime schema validators in production dependencies', () => {
    expect(packageJson.dependencies).toMatchObject({
      ajv: expect.any(String),
      'ajv-formats': expect.any(String),
    })
  })

  it('keeps release metadata out of the final Runtime image to avoid image-digest self-reference', () => {
    const dockerfile = readFileSync(join(process.cwd(), 'agents/Dockerfile.runtime'), 'utf8')
    expect(dockerfile).not.toContain('COPY --chown=node:node agents ./agents')
    expect(dockerfile).toContain('COPY --chown=node:node agents/caffemate-agents ./agents/caffemate-agents')
    expect(dockerfile).toContain('COPY --chown=node:node agents/src ./agents/src')
  })
})
