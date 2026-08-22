export const CAFFEMATE_AGENT_APP_NAME = 'caffemate-agents'

export const AGENT_RUNTIME_CLASS_METHODS = Object.freeze([
  {
    name: 'async_create_session',
    description: 'Creates an ephemeral managed session for one CaffeMate Agent invocation.',
    parameters: {
      type: 'object',
      properties: {
        user_id: { type: 'string' },
        session_id: { type: 'string' },
        state: { type: 'object', nullable: true },
      },
      required: ['user_id', 'session_id'],
    },
    api_mode: 'async',
  },
  {
    name: 'async_stream_query',
    description: 'Streams ADK events for one canonical CaffeMate AgentTask in an existing managed session.',
    parameters: {
      type: 'object',
      properties: {
        user_id: { type: 'string' },
        session_id: { type: 'string' },
        message: { type: 'string' },
      },
      required: ['user_id', 'session_id', 'message'],
    },
    api_mode: 'async_stream',
  },
  {
    name: 'async_ephemeral_stream_query',
    description: 'Creates, executes, and deletes one isolated managed session within a single CaffeMate AgentTask stream.',
    parameters: {
      type: 'object',
      properties: {
        user_id: { type: 'string' },
        session_id: { type: 'string' },
        message: { type: 'string' },
      },
      required: ['user_id', 'session_id', 'message'],
    },
    api_mode: 'async_stream',
  },
  {
    name: 'async_delete_session',
    description: 'Deletes the ephemeral managed session after one CaffeMate Agent invocation.',
    parameters: {
      type: 'object',
      properties: {
        user_id: { type: 'string' },
        session_id: { type: 'string' },
      },
      required: ['user_id', 'session_id'],
    },
    api_mode: 'async',
  },
  {
    name: 'async_get_release_identity',
    description: 'Reads the content-addressed Agent prompt and payload-contract identity from the deployed Runtime artifact.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
    api_mode: 'async',
  },
] as const)
