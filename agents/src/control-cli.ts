#!/usr/bin/env node
import { runAgentControl, runDefaultGcpPreflight, type AgentControlDependencies } from './control'

async function readStdin(): Promise<string> {
  const chunks: string[] = []
  process.stdin.setEncoding('utf8')
  for await (const chunk of process.stdin) chunks.push(String(chunk))
  return chunks.join('').trim()
}

const rawArgs = process.argv.slice(2)
const json = rawArgs.includes('--json')
const accessTokenStdin = rawArgs.includes('--access-token-stdin')
const args = rawArgs.filter((arg) => arg !== '--json' && arg !== '--access-token-stdin')
let dependencies: AgentControlDependencies = {}

if (accessTokenStdin) {
  if (args[0] !== 'gcp-preflight') {
    console.error('ACCESS_TOKEN_STDIN_NOT_ALLOWED: --access-token-stdin is only valid for gcp-preflight')
    process.exitCode = 2
  } else {
    const projectId = process.env.CAFFEMATE_GCP_PROJECT_ID?.trim()
    const accessToken = await readStdin()
    if (!projectId || !accessToken) {
      console.error('GCP_PREFLIGHT_CREDENTIALS_REQUIRED: project id and stdin access token are required')
      process.exitCode = 2
    } else {
      dependencies = {
        gcpPreflight: (modelId?: string) => runDefaultGcpPreflight(modelId, {
          projectId: async () => projectId,
          accessToken: async () => accessToken,
        }),
      }
    }
  }
}

if (!process.exitCode) {
  const output = await runAgentControl(args, dependencies)
  if (json) {
    console.log(JSON.stringify(output))
  } else if (output.ok) {
    console.log(JSON.stringify(output.data, null, 2))
  } else {
    console.error(`${output.code}: ${output.message}`)
  }
  if (!output.ok) process.exitCode = 1
}
