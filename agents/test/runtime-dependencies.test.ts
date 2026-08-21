import { describe, expect, it } from 'vitest'
import packageJson from '../../package.json'

describe('Agent and MCP runtime dependencies', () => {
  it('keeps runtime schema validators in production dependencies', () => {
    expect(packageJson.dependencies).toMatchObject({
      ajv: expect.any(String),
      'ajv-formats': expect.any(String),
    })
  })
})
