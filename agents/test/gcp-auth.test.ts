import { describe, expect, it } from 'vitest'
import { createGoogleCloudContext } from '../src/gcp-auth'

describe('Google Cloud ADC context', () => {
  it('resolves project id and returns a non-empty bearer token', async () => {
    const context = createGoogleCloudContext({
      getProjectId: async () => 'proj-aj20-211200020328',
      getAccessToken: async () => 'adc-token',
    })

    await expect(context.projectId()).resolves.toBe('proj-aj20-211200020328')
    await expect(context.accessToken()).resolves.toBe('adc-token')
  })

  it('fails closed when ADC cannot resolve a project or token', async () => {
    const missingProject = createGoogleCloudContext({
      getProjectId: async () => '',
      getAccessToken: async () => 'adc-token',
    })
    const missingToken = createGoogleCloudContext({
      getProjectId: async () => 'proj-aj20-211200020328',
      getAccessToken: async () => null,
    })

    await expect(missingProject.projectId()).rejects.toMatchObject({ code: 'GCP_PROJECT_UNRESOLVED' })
    await expect(missingToken.accessToken()).rejects.toMatchObject({ code: 'GCP_ACCESS_TOKEN_UNRESOLVED' })
  })
})