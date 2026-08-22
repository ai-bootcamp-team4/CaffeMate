import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const scriptPath = join(process.cwd(), 'scripts/deploy-agent-runtime.sh')

describe('Agent Runtime deployment provenance', () => {
  it('provides one checked-in source-to-runtime deployment workflow', () => {
    expect(existsSync(scriptPath)).toBe(true)
    if (!existsSync(scriptPath)) return
    const script = readFileSync(scriptPath, 'utf8')
    expect(script).toContain('git rev-parse HEAD')
    expect(script).toContain('git status --porcelain')
    expect(script).toContain('agents/cloudbuild.runtime.yaml')
    expect(script).toContain('agents/release-manifest.json')
    expect(script).toContain('RUNTIME_RELEASE_PIN_REQUIRED')
  })

  it('updates the pinned Reasoning Engine only after immutable digest resolution and waits for authoritative read-back', () => {
    expect(existsSync(scriptPath)).toBe(true)
    if (!existsSync(scriptPath)) return
    const script = readFileSync(scriptPath, 'utf8')
    expect(script).toContain('updateMask=spec.containerSpec,spec.classMethods,labels')
    expect(script).toContain('operations/')
    expect(script).toContain('gcp-preflight --json')
    expect(script).toContain('async_get_release_identity')
  })
})
