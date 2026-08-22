export interface RuntimeErrorRecord {
  event: 'RUNTIME_STREAM_EXECUTION_FAILED'
  error_name: string
  error_message: string
}

function bounded(value: string): string {
  return value.replace(/[\r\n]+/g, ' ').slice(0, 500)
}

export function runtimeErrorRecord(error: unknown): RuntimeErrorRecord {
  return {
    event: 'RUNTIME_STREAM_EXECUTION_FAILED',
    error_name: bounded(error instanceof Error ? error.name : 'UnknownError'),
    error_message: bounded(error instanceof Error ? error.message : String(error)),
  }
}
