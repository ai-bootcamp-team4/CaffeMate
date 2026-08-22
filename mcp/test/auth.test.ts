import { createHmac } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { createProductionAuthorizer, type IdentityVerifier } from '../src/auth'

const secret = 'scope-secret-with-at-least-thirty-two-bytes'
const now = 1_787_270_400

function scopeToken(overrides: Record<string, unknown> = {}): string {
  const header = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url')
  const payload = Buffer.from(JSON.stringify({
    iss: 'caffemate-control-api', aud: 'caffemate-mcp', venture_project_id: 'project-1',
    workflow_run_id: 'workflow-1', full_head_digest: `sha256:${'a'.repeat(64)}`, jti: 'jti-1',
    iat: now, exp: now + 300, ...overrides,
  })).toString('base64url')
  const signature = createHmac('sha256', secret).update(`${header}.${payload}`).digest('base64url')
  return `${header}.${payload}.${signature}`
}

function verifier(payload: Record<string, unknown>): IdentityVerifier {
  return { verify: async () => payload as never }
}

function request(token = scopeToken()): Request {
  return new Request('https://mcp.example/mcp', {
    headers: { Authorization: 'Bearer google-id-token', 'X-CaffeMate-Scope-Token': token },
  })
}

describe('production MCP authorizer', () => {
  it('accepts only the configured verified Cloud Run caller and a valid bounded scope', async () => {
    const authorize = createProductionAuthorizer({
      audience: 'https://mcp.example', allowedCallerEmail: 'api@example.iam.gserviceaccount.com',
      scopeSecret: secret, identityVerifier: verifier({ email: 'api@example.iam.gserviceaccount.com', email_verified: true }), now: () => now,
    })
    await expect(authorize(request())).resolves.toEqual({ ventureProjectId: 'project-1', workflowRunId: 'workflow-1' })
  })

  it('rejects a different service identity', async () => {
    const authorize = createProductionAuthorizer({
      audience: 'https://mcp.example', allowedCallerEmail: 'api@example.iam.gserviceaccount.com', scopeSecret: secret,
      identityVerifier: verifier({ email: 'other@example.iam.gserviceaccount.com', email_verified: true }), now: () => now,
    })
    await expect(authorize(request())).rejects.toMatchObject({ status: 403, code: 'MCP_CALLER_NOT_ALLOWED' })
  })

  it.each([
    [{ exp: now }, 'expired'],
    [{ aud: 'other-audience' }, 'wrong audience'],
    [{ exp: now + 301 }, 'overlong lifetime'],
  ])('rejects an invalid scope token: %s (%s)', async (claims, _label) => {
    expect(_label).toBeTypeOf('string')
    const authorize = createProductionAuthorizer({
      audience: 'https://mcp.example', allowedCallerEmail: 'api@example.iam.gserviceaccount.com', scopeSecret: secret,
      identityVerifier: verifier({ email: 'api@example.iam.gserviceaccount.com', email_verified: true }), now: () => now,
    })
    await expect(authorize(request(scopeToken(claims)))).rejects.toMatchObject({ status: 403, code: 'MCP_SCOPE_TOKEN_INVALID' })
  })
})
