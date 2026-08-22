import { createHmac, timingSafeEqual } from 'node:crypto'
import { OAuth2Client, type TokenPayload } from 'google-auth-library'
import { McpAuthorizationError, type AuthorizedMcpScope, type McpRequestAuthorizer } from './server'

const MAX_SCOPE_TTL_SECONDS = 300

interface ScopeClaims {
  iss: string
  aud: string
  venture_project_id: string
  workflow_run_id: string
  full_head_digest: string
  jti: string
  iat: number
  exp: number
}

export interface IdentityVerifier {
  verify(token: string, audience: string): Promise<TokenPayload>
}

class GoogleIdentityVerifier implements IdentityVerifier {
  private readonly client = new OAuth2Client()

  async verify(token: string, audience: string): Promise<TokenPayload> {
    const ticket = await this.client.verifyIdToken({ idToken: token, audience })
    const payload = ticket.getPayload()
    if (!payload) throw new Error('identity token payload is missing')
    return payload
  }
}

export interface ProductionAuthorizerOptions {
  audience: string
  allowedCallerEmail: string
  scopeSecret: string
  identityVerifier?: IdentityVerifier
  now?: () => number
}

function decodeJsonSegment(segment: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(Buffer.from(segment, 'base64url').toString('utf8'))
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('not an object')
    return value as Record<string, unknown>
  } catch {
    throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'scope token JSON is invalid')
  }
}

function readBearer(request: Request): string {
  const value = request.headers.get('authorization')
  if (!value?.startsWith('Bearer ') || value.length <= 7) {
    throw new McpAuthorizationError(401, 'MCP_IDENTITY_TOKEN_MISSING', 'Google identity token is required')
  }
  return value.slice(7)
}

function verifyScopeToken(token: string, secret: string, now: number): ScopeClaims {
  const parts = token.split('.')
  if (parts.length !== 3) throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'scope token is malformed')
  const [encodedHeader, encodedPayload, encodedSignature] = parts
  const header = decodeJsonSegment(encodedHeader)
  if (header.alg !== 'HS256' || header.typ !== 'JWT') {
    throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'scope token algorithm is not allowed')
  }
  const expected = createHmac('sha256', secret).update(`${encodedHeader}.${encodedPayload}`).digest()
  let actual: Buffer
  try {
    actual = Buffer.from(encodedSignature, 'base64url')
  } catch {
    throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'scope token signature is malformed')
  }
  if (expected.length !== actual.length || !timingSafeEqual(expected, actual)) {
    throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'scope token signature is invalid')
  }

  const payload = decodeJsonSegment(encodedPayload)
  const requiredStrings = ['venture_project_id', 'workflow_run_id', 'full_head_digest', 'jti'] as const
  if (payload.iss !== 'caffemate-control-api' || payload.aud !== 'caffemate-mcp'
    || !requiredStrings.every((name) => typeof payload[name] === 'string' && payload[name].length > 0)
    || typeof payload.iat !== 'number' || typeof payload.exp !== 'number') {
    throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'scope token claims are invalid')
  }
  if (!/^sha256:[0-9a-f]{64}$/.test(payload.full_head_digest as string)
    || payload.exp <= now || payload.iat > now + 30
    || payload.exp <= payload.iat || payload.exp - payload.iat > MAX_SCOPE_TTL_SECONDS) {
    throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_INVALID', 'scope token lifetime or fence is invalid')
  }
  return payload as unknown as ScopeClaims
}

export function createProductionAuthorizer(options: ProductionAuthorizerOptions): McpRequestAuthorizer {
  if (!options.audience || !options.allowedCallerEmail || Buffer.byteLength(options.scopeSecret) < 32) {
    throw new Error('MCP_AUTH_CONFIGURATION_INVALID')
  }
  const verifier = options.identityVerifier ?? new GoogleIdentityVerifier()
  const now = options.now ?? (() => Math.floor(Date.now() / 1000))
  return async (request): Promise<AuthorizedMcpScope> => {
    let payload: TokenPayload
    try {
      payload = await verifier.verify(readBearer(request), options.audience)
    } catch (error) {
      if (error instanceof McpAuthorizationError) throw error
      throw new McpAuthorizationError(401, 'MCP_IDENTITY_TOKEN_INVALID', 'Google identity token is invalid')
    }
    if (payload.email !== options.allowedCallerEmail || payload.email_verified !== true) {
      throw new McpAuthorizationError(403, 'MCP_CALLER_NOT_ALLOWED', 'caller identity is not allowed')
    }
    const scopeToken = request.headers.get('X-CaffeMate-Scope-Token')
    if (!scopeToken) throw new McpAuthorizationError(403, 'MCP_SCOPE_TOKEN_MISSING', 'scope token is required')
    const claims = verifyScopeToken(scopeToken, options.scopeSecret, now())
    return { ventureProjectId: claims.venture_project_id, workflowRunId: claims.workflow_run_id }
  }
}
