import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { Readable } from 'node:stream'
import { createProductionAuthorizer } from './auth'
import { createConnectorRegistry } from './connectors'
import { createCaffeMateMcpHttpHandler } from './server'

function required(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`${name}_REQUIRED`)
  return value
}

const handler = createCaffeMateMcpHttpHandler({
  authorize: createProductionAuthorizer({
    audience: required('MCP_AUDIENCE'),
    allowedCallerEmail: required('MCP_ALLOWED_CALLER_EMAIL'),
    scopeSecret: required('MCP_SCOPE_HMAC_SECRET'),
  }),
  connectors: createConnectorRegistry({ jusoApiKey: process.env.JUSO_API_KEY }),
})

async function writeResponse(response: Response, target: ServerResponse): Promise<void> {
  target.statusCode = response.status
  response.headers.forEach((value, name) => target.setHeader(name, value))
  if (!response.body) {
    target.end()
    return
  }
  await new Promise<void>((resolve, reject) => {
    Readable.fromWeb(response.body as never).once('error', reject).pipe(target).once('finish', resolve).once('error', reject)
  })
}

async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const url = new URL(request.url ?? '/', `http://${request.headers.host ?? 'localhost'}`)
  if (url.pathname === '/healthz') {
    await writeResponse(Response.json({ status: 'ok' }), response)
    return
  }
  if (url.pathname !== '/mcp') {
    await writeResponse(new Response('Not found', { status: 404 }), response)
    return
  }
  const method = request.method ?? 'GET'
  const body = method === 'GET' || method === 'HEAD' ? undefined : Readable.toWeb(request) as ReadableStream<Uint8Array>
  const headers = new Headers()
  for (const [name, value] of Object.entries(request.headers)) {
    if (Array.isArray(value)) value.forEach((item) => headers.append(name, item))
    else if (value !== undefined) headers.set(name, value)
  }
  const init: RequestInit & { duplex?: 'half' } = { method, headers, body }
  if (body) init.duplex = 'half'
  const webRequest = new Request(url, init)
  await writeResponse(await handler.fetch(webRequest), response)
}

const port = Number(process.env.PORT ?? '8080')
const server = createServer((request, response) => {
  handle(request, response).catch((error) => {
    console.error('MCP_REQUEST_FAILED', error instanceof Error ? error.message : 'unknown')
    if (!response.headersSent) response.writeHead(500, { 'content-type': 'application/json' })
    response.end(JSON.stringify({ error: 'MCP_INTERNAL_ERROR' }))
  })
})

server.listen(port, '0.0.0.0', () => console.log(`caffemate-mcp listening on ${port}`))

async function shutdown(): Promise<void> {
  server.close()
  await handler.close()
}
process.once('SIGTERM', () => void shutdown())
process.once('SIGINT', () => void shutdown())
