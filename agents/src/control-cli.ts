#!/usr/bin/env node
import { runAgentControl } from './control'

const rawArgs = process.argv.slice(2)
const json = rawArgs.includes('--json')
const args = rawArgs.filter((arg) => arg !== '--json')
const output = await runAgentControl(args)

if (json) {
  console.log(JSON.stringify(output))
} else if (output.ok) {
  console.log(JSON.stringify(output.data, null, 2))
} else {
  console.error(`${output.code}: ${output.message}`)
}

if (!output.ok) process.exitCode = 1
