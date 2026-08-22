import { describe, expect, it } from 'vitest'
import { runtimeErrorRecord } from '../src/runtime-error'

describe('Agent Runtime error telemetry', () => {
  it('records only bounded error identity without request contents', () => {
    const record = runtimeErrorRecord(new Error(`generation failed\n${'x'.repeat(1000)}`))

    expect(record.event).toBe('RUNTIME_STREAM_EXECUTION_FAILED')
    expect(record.error_name).toBe('Error')
    expect(record.error_message).not.toContain('\n')
    expect(record.error_message.length).toBe(500)
    expect(Object.keys(record)).toEqual(['event', 'error_name', 'error_message'])
  })
})
