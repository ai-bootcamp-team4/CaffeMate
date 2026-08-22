import { GoogleAuth } from 'google-auth-library'

export interface GoogleCloudContext {
  accessToken(): Promise<string>
}

export function createApplicationDefaultGoogleCloudContext(): GoogleCloudContext {
  const auth = new GoogleAuth({ scopes: ['https://www.googleapis.com/auth/cloud-platform'] })
  return {
    async accessToken(): Promise<string> {
      const token = await auth.getAccessToken()
      if (!token?.trim()) {
        throw new Error('GCP_ACCESS_TOKEN_UNRESOLVED: Application Default Credentials did not return an access token')
      }
      return token
    },
  }
}
