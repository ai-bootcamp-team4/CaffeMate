import { describe, expect, it } from 'vitest'
import {
  flushTraceProvider,
  ragSignalContract,
  safeAgentSpanAttributes,
  traceCarrierFromTask,
} from '../src/telemetry'
import type { AgentTask } from '../src/types'

const task = {
  agent_name: 'PROPOSAL_AGENT',
  task_type: 'PROPOSE_INDEPENDENT',
  prompt_version: 'proposal-v3',
  input_schema_id: 'agent-task-v1',
  output_schema_id: 'agent-result-v1',
  venture_project_id: 'secret-project',
  workflow_run_id: 'secret-workflow',
  payload: { target_area_input: 'exact location' },
  trace_context: {
    traceparent: '00-0123456789abcdef0123456789abcdef-0123456789abcdef-01',
  },
} as unknown as AgentTask

describe('AgentOps telemetry contract', () => {
  it('keeps release metadata and excludes business payloads and identifiers', () => {
    expect(safeAgentSpanAttributes(task, {
      modelId: 'gemini-2.5-flash',
      sourceRevision: 'abc123',
    })).toEqual({
      'caffemate.agent.role': 'PROPOSAL_AGENT',
      'caffemate.agent.task_type': 'PROPOSE_INDEPENDENT',
      'caffemate.prompt.version': 'proposal-v3',
      'caffemate.schema.input': 'agent-task-v1',
      'caffemate.schema.output': 'agent-result-v1',
      'gen_ai.request.model': 'gemini-2.5-flash',
      'service.version': 'abc123',
    })
  })

  it('accepts only a valid W3C trace carrier from an AgentTask', () => {
    expect(traceCarrierFromTask(task)).toEqual(task.trace_context)
    expect(traceCarrierFromTask({ ...task, trace_context: { traceparent: 'bad' } })).toEqual({})
  })

  it('defines RAG instruments without fake defaults', () => {
    expect(Object.keys(ragSignalContract)).toEqual([
      'caffemate.rag.retrieve.duration',
      'caffemate.rag.rerank.duration',
      'caffemate.rag.hits',
      'caffemate.rag.evidence.accepted',
      'caffemate.rag.citations',
    ])
    expect(JSON.stringify(ragSignalContract)).not.toContain('default')
  })

  it('waits for the trace provider to export finished managed-runtime spans', async () => {
    const calls: string[] = []
    await flushTraceProvider({
      forceFlush: async () => {
        calls.push('flushed')
      },
    })
    expect(calls).toEqual(['flushed'])
  })
})
