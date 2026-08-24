import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const buildPath = join(process.cwd(), 'scripts/build-agent-runtime-release.sh')
const approvePath = join(process.cwd(), 'scripts/approve-agent-runtime-release.sh')
const deployPath = join(process.cwd(), 'scripts/deploy-agent-runtime.sh')
const verifyPath = join(process.cwd(), 'scripts/verify-agent-runtime-deployment.sh')
const standardVerifyPath = join(process.cwd(), 'scripts/verify-api-worker-runtime.sh')

function script(path: string): string {
  expect(existsSync(path), path).toBe(true)
  return readFileSync(path, 'utf8')
}

describe('Agent Runtime deployment provenance', () => {
  it('separates immutable build, approval and deployment while binding all stages to one source revision', () => {
    const build = script(buildPath)
    const approve = script(approvePath)
    const deploy = script(deployPath)

    expect(build).toContain('git rev-parse HEAD')
    expect(build).toContain('git status --porcelain')
    expect(build).toContain('agents/cloudbuild.runtime.yaml')
    expect(build).toContain('_SOURCE_REVISION=${source_revision}')
    expect(build).toContain('verified_build_id_for_image')

    expect(approve).toContain('approved-${source_revision}')
    expect(approve).toContain('verified_build_id_for_image')
    expect(deploy).toContain('agents/release-manifest.json')
    expect(deploy).toContain('approved-${source_revision}')
    expect(deploy).toContain('verified_build_id_for_image')
    expect(deploy).toContain('{"name": "CAFFEMATE_GCP_PROJECT_ID", "value": os.environ["PROJECT_ID"]}')
  })

  it('updates only the pinned Reasoning Engine and performs authoritative deployment plus release preflight verification', () => {
    const deploy = script(deployPath)
    const verify = script(verifyPath)
    const standardVerify = script(standardVerifyPath)

    expect(deploy).toContain('manifest["runtime"]["resource_name"]')
    expect(deploy).toContain('request PATCH')
    expect(deploy).toContain('updateMask=description,labels,spec.classMethods,spec.deploymentSpec,spec.agentFramework,spec.identityType,spec.containerSpec.imageUri')
    expect(deploy).toContain('operation_name=')
    expect(deploy).toContain('./scripts/verify-agent-runtime-deployment.sh')
    expect(verify).toContain('EXPECTED_IMAGE')
    expect(verify).toContain('SOURCE_REVISION')
    expect(verify).toContain('BUILD_ID')
    expect(standardVerify).toContain('caffemate-agent-gcp-release-preflight')
    expect(standardVerify).toContain('agents/src/control-cli.ts,gcp-preflight,--json')
  })
})
