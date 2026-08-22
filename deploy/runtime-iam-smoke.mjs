import { GoogleAuth } from 'google-auth-library'

const region = process.env.CAFFEMATE_GCP_REGION ?? 'asia-northeast3'
const resources = [
  {
    name: process.env.AGENT_RUNTIME_RESOURCE,
    required: [],
    permissions: [
      'aiplatform.reasoningEngines.update',
      'aiplatform.reasoningEngines.delete',
    ],
  },
  {
    name: process.env.RAG_CORPUS_RESOURCE,
    required: ['aiplatform.ragCorpora.query'],
    permissions: [
      'aiplatform.ragCorpora.query',
      'aiplatform.ragCorpora.update',
      'aiplatform.ragCorpora.delete',
    ],
  },
  {
    name: process.env.RAG_FILE_RESOURCE,
    required: [],
    permissions: ['aiplatform.ragFiles.delete'],
  },
]

if (resources.some(({ name }) => !name)) {
  throw new Error('Agent Runtime, RAG corpus and RAG file resources are required')
}

const auth = new GoogleAuth({
  scopes: ['https://www.googleapis.com/auth/cloud-platform'],
})
const client = await auth.getClient()
const accessToken = await client.getAccessToken()
const token = typeof accessToken === 'string' ? accessToken : accessToken?.token
if (!token) {
  throw new Error('Google access token is unavailable')
}

for (const { name, required, permissions } of resources) {
  const response = await fetch(
    `https://${region}-aiplatform.googleapis.com/v1/${name}:testIamPermissions`,
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
    throw new Error(`testIamPermissions failed for ${name}: HTTP ${response.status}`)
  }
  const result = await response.json()
  const granted = Array.isArray(result.permissions) ? result.permissions : []
  const missing = required.filter((permission) => !granted.includes(permission))
  if (missing.length > 0) {
    throw new Error(`MCP identity lacks required permissions on ${name}: ${missing.join(',')}`)
  }
  const prohibited = granted.filter((permission) => !required.includes(permission))
  if (prohibited.length > 0) {
    throw new Error(`MCP identity has prohibited permissions on ${name}: ${prohibited.join(',')}`)
  }
}

console.log('MCP_EFFECTIVE_IAM_OK')
