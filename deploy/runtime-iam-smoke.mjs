import { GoogleAuth } from 'google-auth-library'

const projectId = process.env.CAFFEMATE_GCP_PROJECT_ID
if (!projectId) {
  throw new Error('CAFFEMATE_GCP_PROJECT_ID is required')
}

const required = [
  'aiplatform.endpoints.predict',
  'aiplatform.ragCorpora.get',
  'aiplatform.ragCorpora.query',
  'aiplatform.ragFiles.get',
  'discoveryengine.rankingConfigs.rank',
]
const prohibited = [
  'aiplatform.reasoningEngines.update',
  'aiplatform.reasoningEngines.delete',
  'aiplatform.ragCorpora.update',
  'aiplatform.ragCorpora.delete',
  'aiplatform.ragFiles.delete',
]
const permissions = [...required, ...prohibited]

const auth = new GoogleAuth({
  scopes: ['https://www.googleapis.com/auth/cloud-platform'],
})
const client = await auth.getClient()
const accessToken = await client.getAccessToken()
const token = typeof accessToken === 'string' ? accessToken : accessToken?.token
if (!token) {
  throw new Error('Google access token is unavailable')
}

const response = await fetch(
  `https://cloudresourcemanager.googleapis.com/v1/projects/${projectId}:testIamPermissions`,
  {
    method: 'POST',
    headers: {
      authorization: `Bearer ${token}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify({ permissions }),
    signal: AbortSignal.timeout(30_000),
  },
)
if (!response.ok) {
  throw new Error(`project testIamPermissions failed: HTTP ${response.status}`)
}
const result = await response.json()
const granted = Array.isArray(result.permissions) ? result.permissions : []
const missing = required.filter((permission) => !granted.includes(permission))
if (missing.length > 0) {
  throw new Error(`MCP identity lacks required permissions: ${missing.join(',')}`)
}
const unexpected = prohibited.filter((permission) => granted.includes(permission))
if (unexpected.length > 0) {
  throw new Error(`MCP identity has prohibited permissions: ${unexpected.join(',')}`)
}

console.log('MCP_EFFECTIVE_IAM_OK')
