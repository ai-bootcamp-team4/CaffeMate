import { GoogleAuth } from 'google-auth-library'

export class GoogleCloudAuthError extends Error {
  constructor(public readonly code: string, message: string) {
    super(`${code}: ${message}`)
    this.name = 'GoogleCloudAuthError'
  }
}

export interface GoogleCloudCredentialSource {
  getProjectId(): Promise<string>
  getAccessToken(): Promise<string | null | undefined>
}

export interface GoogleCloudContext {
  projectId(): Promise<string>
  accessToken(): Promise<string>
}

export function createGoogleCloudContext(source: GoogleCloudCredentialSource): GoogleCloudContext {
  return {
    async projectId(): Promise<string> {
      const projectId = (await source.getProjectId()).trim()
      if (!projectId) {
        throw new GoogleCloudAuthError('GCP_PROJECT_UNRESOLVED', 'Application Default Credentials did not resolve a project id')
      }
      return projectId
    },
    async accessToken(): Promise<string> {
      const token = await source.getAccessToken()
      if (!token?.trim()) {
        throw new GoogleCloudAuthError('GCP_ACCESS_TOKEN_UNRESOLVED', 'Application Default Credentials did not return an access token')
      }
      return token
    },
  }
}

export function createApplicationDefaultGoogleCloudContext(): GoogleCloudContext {
  const auth = new GoogleAuth({ scopes: ['https://www.googleapis.com/auth/cloud-platform'] })
  return createGoogleCloudContext(auth)
}