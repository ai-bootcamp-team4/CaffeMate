import { createRequire } from 'node:module'
import {
  context,
  propagation,
  SpanStatusCode,
  trace,
  type Span,
  type Tracer,
} from '@opentelemetry/api'
import type { AgentTask } from './types'

type SafeAttributes = Record<string, string | number | boolean>
type AsyncAction<T> = () => Promise<T>
interface FlushableTraceProvider {
  forceFlush(): Promise<void>
}

const TRACEPARENT = /^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$/
const require = createRequire(import.meta.url)

let agentTracer: Tracer | undefined
let agentTraceProvider: FlushableTraceProvider | undefined
let initialized = false

export const ragSignalContract = Object.freeze({
  'caffemate.rag.retrieve.duration': { instrument: 'histogram', unit: 'ms', attributes: ['source_family', 'result_status', 'index_generation'] },
  'caffemate.rag.rerank.duration': { instrument: 'histogram', unit: 'ms', attributes: ['source_family', 'result_status', 'index_generation'] },
  'caffemate.rag.hits': { instrument: 'histogram', unit: '1', attributes: ['source_family', 'result_status', 'index_generation'] },
  'caffemate.rag.evidence.accepted': { instrument: 'counter', unit: '1', attributes: ['source_family', 'result_status', 'index_generation'] },
  'caffemate.rag.citations': { instrument: 'counter', unit: '1', attributes: ['source_family', 'result_status', 'index_generation'] },
})

export function traceProjectIdFromEnv(env: NodeJS.ProcessEnv = process.env): string | undefined {
  return env.CAFFEMATE_GCP_PROJECT_ID || env.GOOGLE_CLOUD_PROJECT
}

export function traceCarrierFromTask(task: AgentTask): Record<string, string> {
  const value = task.trace_context
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const record = value as Record<string, unknown>
  if (typeof record.traceparent !== 'string' || !TRACEPARENT.test(record.traceparent)) return {}
  return {
    traceparent: record.traceparent,
    ...(typeof record.tracestate === 'string' && record.tracestate.length <= 512
      ? { tracestate: record.tracestate }
      : {}),
  }
}

export function safeAgentSpanAttributes(
  task: AgentTask,
  release: { modelId?: string; sourceRevision?: string } = {},
): SafeAttributes {
  const values: Record<string, unknown> = {
    'caffemate.agent.role': task.agent_name,
    'caffemate.agent.task_type': task.task_type,
    'caffemate.prompt.version': task.prompt_version,
    'caffemate.schema.input': task.input_schema_id,
    'caffemate.schema.output': task.output_schema_id,
    'gen_ai.request.model': release.modelId,
    'service.version': release.sourceRevision,
  }
  return Object.fromEntries(
    Object.entries(values)
      .filter((entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1].length > 0)
      .map(([key, value]) => [key, value.slice(0, 256)]),
  )
}

export function initializeAgentTelemetry(env: NodeJS.ProcessEnv = process.env): void {
  if (initialized || !['1', 'true'].includes((env.CAFFEMATE_OTEL_ENABLED ?? '').toLowerCase())) return
  const { NodeTracerProvider } = require('@opentelemetry/sdk-trace-node') as typeof import('@opentelemetry/sdk-trace-node')
  const { SimpleSpanProcessor } = require('@opentelemetry/sdk-trace-base') as typeof import('@opentelemetry/sdk-trace-base')
  const { resourceFromAttributes } = require('@opentelemetry/resources') as typeof import('@opentelemetry/resources')
  const { TraceExporter } = require('@google-cloud/opentelemetry-cloud-trace-exporter') as typeof import('@google-cloud/opentelemetry-cloud-trace-exporter')
  const resource = resourceFromAttributes({
    'service.name': 'caffemate-agent-runtime',
    'service.version': env.CAFFEMATE_SOURCE_REVISION ?? env.K_REVISION ?? 'unknown',
    'deployment.environment.name': env.CAFFEMATE_ENVIRONMENT ?? 'unknown',
  })
  const provider = new NodeTracerProvider({
    resource,
    // User intent: finish each managed Agent span in Cloud Trace while the
    // request still owns CPU; deferred timers are unreliable on serverless.
    spanProcessors: [new SimpleSpanProcessor(new TraceExporter({ projectId: traceProjectIdFromEnv(env) }))],
  })
  provider.register()
  agentTraceProvider = provider
  agentTracer = trace.getTracer('caffemate.agent-runtime')
  initialized = true
}

export async function flushTraceProvider(
  provider: FlushableTraceProvider | undefined = agentTraceProvider,
): Promise<void> {
  if (provider) await provider.forceFlush()
}

export async function withAgentTaskSpan<T>(
  task: AgentTask,
  name: string,
  action: AsyncAction<T>,
  attributes: SafeAttributes = {},
): Promise<T> {
  const activeTracer = agentTracer
  if (!initialized || !activeTracer) return action()
  const parent = propagation.extract(context.active(), traceCarrierFromTask(task))
  return activeTracer.startActiveSpan(
    name,
    {
      attributes: {
        ...safeAgentSpanAttributes(task, {
          modelId: process.env.AGENT_MODEL_ID,
          sourceRevision: process.env.CAFFEMATE_SOURCE_REVISION ?? process.env.K_REVISION,
        }),
        ...attributes,
      },
    },
    parent,
    async (span: Span) => {
      try {
        return await action()
      } catch (error) {
        span.recordException(error instanceof Error ? error : new Error(String(error)))
        span.setStatus({ code: SpanStatusCode.ERROR })
        throw error
      } finally {
        span.end()
        // User intent: the managed Runtime response must not finish before
        // the dispatch and Gemini child spans reach Cloud Trace.
        await flushTraceProvider()
      }
    },
  )
}

export async function withActiveSpan<T>(
  name: string,
  attributes: SafeAttributes,
  action: AsyncAction<T>,
): Promise<T> {
  const activeTracer = agentTracer
  if (!initialized || !activeTracer) return action()
  return activeTracer.startActiveSpan(name, { attributes }, async (span: Span) => {
    try {
      return await action()
    } catch (error) {
      span.recordException(error instanceof Error ? error : new Error(String(error)))
      span.setStatus({ code: SpanStatusCode.ERROR })
      throw error
    } finally {
      span.end()
    }
  })
}

export function injectCurrentTrace(headers: Record<string, string>): Record<string, string> {
  if (!initialized || !agentTracer) return headers
  const carrier = { ...headers }
  propagation.inject(context.active(), carrier)
  return carrier
}

export function setCurrentSpanAttributes(attributes: SafeAttributes): void {
  if (!initialized || !agentTracer) return
  const span = trace.getActiveSpan()
  if (span) span.setAttributes(attributes)
}
