import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const runtimePackageDir = join(process.cwd(), 'agents/runtime-package')

function readJson(path: string): unknown {
  return JSON.parse(readFileSync(path, 'utf8'))
}

describe('Agent Runtime production dependency boundary', () => {
  it('pins the Node base image digest for reproducible release inputs', () => {
    const dockerfile = readFileSync(join(process.cwd(), 'agents/Dockerfile.runtime'), 'utf8')
    const pinnedBase =
      'node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32'

    expect(dockerfile).toContain(`FROM ${pinnedBase} AS build`)
    expect(dockerfile).toContain(`FROM ${pinnedBase} AS runtime`)
    expect(dockerfile).not.toMatch(/^FROM node:22-alpine AS /m)
  })

  it('pins only the dependencies imported by the Runtime artifact', () => {
    const runtimePackage = readJson(join(runtimePackageDir, 'package.json')) as {
      dependencies?: Record<string, string>
      devDependencies?: Record<string, string>
      overrides?: Record<string, string>
    }

    expect(runtimePackage.dependencies).toEqual({
      '@google-cloud/opentelemetry-cloud-trace-exporter': '3.1.0',
      '@google/adk': '1.6.0',
      '@google/adk-devtools': '1.6.0',
      '@opentelemetry/api': '1.9.1',
      '@opentelemetry/resources': '2.10.0',
      '@opentelemetry/sdk-trace-base': '2.10.0',
      '@opentelemetry/sdk-trace-node': '2.10.0',
      ajv: '8.20.0',
      'ajv-formats': '3.0.1',
      'google-auth-library': '11.0.2',
    })
    expect(runtimePackage.devDependencies).toEqual({
      esbuild: '0.28.2',
    })
    expect(runtimePackage.overrides).toEqual({
      'adm-zip': '0.6.0',
      tar: '7.5.22',
    })
  })

  it('locks the dedicated Runtime package independently from the repository root', () => {
    const runtimeLock = readJson(join(runtimePackageDir, 'package-lock.json')) as {
      packages?: Record<string, { dependencies?: Record<string, string>; devDependencies?: Record<string, string> }>
    }
    expect(runtimeLock.packages?.['']?.dependencies).toEqual({
      '@google-cloud/opentelemetry-cloud-trace-exporter': '3.1.0',
      '@google/adk': '1.6.0',
      '@google/adk-devtools': '1.6.0',
      '@opentelemetry/api': '1.9.1',
      '@opentelemetry/resources': '2.10.0',
      '@opentelemetry/sdk-trace-base': '2.10.0',
      '@opentelemetry/sdk-trace-node': '2.10.0',
      ajv: '8.20.0',
      'ajv-formats': '3.0.1',
      'google-auth-library': '11.0.2',
    })
    expect(runtimeLock.packages?.['']?.devDependencies).toEqual({
      esbuild: '0.28.2',
    })
  })

  it('installs the dedicated Runtime closure in both Docker stages', () => {
    const dockerfile = readFileSync(join(process.cwd(), 'agents/Dockerfile.runtime'), 'utf8')

    expect(dockerfile).toContain(
      'COPY agents/runtime-package/package.json agents/runtime-package/package-lock.json ./',
    )
    expect(dockerfile).toContain(
      'COPY --chown=node:node agents/runtime-package/package.json agents/runtime-package/package-lock.json ./',
    )
    expect(dockerfile).not.toContain('COPY package.json package-lock.json ./')
    expect(dockerfile).toContain('RUN npm ci --omit=dev')
  })

  it('keeps release metadata out of the final Runtime image to avoid image-digest self-reference', () => {
    const dockerfile = readFileSync(join(process.cwd(), 'agents/Dockerfile.runtime'), 'utf8')
    expect(dockerfile).not.toContain('COPY --chown=node:node agents ./agents')
    expect(dockerfile).toContain('COPY --chown=node:node agents/caffemate-agents ./agents/caffemate-agents')
    expect(dockerfile).toContain('COPY --chown=node:node agents/src ./agents/src')
  })
})
