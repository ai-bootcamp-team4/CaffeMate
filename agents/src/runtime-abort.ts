interface RuntimeAbortRequest {
  on(event: 'aborted', listener: () => void): unknown
}

interface RuntimeAbortResponse {
  on(event: 'close', listener: () => void): unknown
}

export function bindRuntimeStreamAbort(
  request: RuntimeAbortRequest,
  response: RuntimeAbortResponse,
  isResponseCompleted: () => boolean,
): AbortController {
  const controller = new AbortController()
  const abortIncompleteStream = () => {
    if (!isResponseCompleted()) controller.abort()
  }
  request.on('aborted', abortIncompleteStream)
  response.on('close', abortIncompleteStream)
  return controller
}
