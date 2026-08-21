import commonTypesSchema from '../../docs/contracts/common-types.schema.json'
import evidenceRecordSchema from '../../docs/contracts/evidence-record.schema.json'
import mcpToolContractsSchema from '../../docs/contracts/mcp-tool-contracts.schema.json'
import type { McpToolDefinition } from './manifest'

type JsonObject = Record<string, unknown>
type JsonValue = null | boolean | number | string | JsonValue[] | JsonObject

const documents: Record<string, JsonObject> = {
  'mcp-tool-contracts.schema.json': mcpToolContractsSchema as JsonObject,
  'evidence-record.schema.json': evidenceRecordSchema as JsonObject,
  'common-types.schema.json': commonTypesSchema as JsonObject,
}

function decodePointerToken(token: string): string {
  return token.replaceAll('~1', '/').replaceAll('~0', '~')
}

function resolvePointer(document: JsonObject, pointer: string): unknown {
  if (pointer === '' || pointer === '/') return document
  if (!pointer.startsWith('/')) throw new Error(`MCP_SCHEMA_POINTER_INVALID: ${pointer}`)
  let current: unknown = document
  for (const token of pointer.slice(1).split('/').map(decodePointerToken)) {
    if (!current || typeof current !== 'object' || Array.isArray(current) || !(token in current)) {
      throw new Error(`MCP_SCHEMA_POINTER_NOT_FOUND: ${pointer}`)
    }
    current = (current as JsonObject)[token]
  }
  return current
}

function resolveReference(reference: string, currentDocument: string): { documentName: string; pointer: string; value: unknown } {
  const [rawDocument, rawFragment = ''] = reference.split('#', 2)
  const documentName = rawDocument ? rawDocument.replace(/^\.\//, '') : currentDocument
  const document = documents[documentName]
  if (!document) throw new Error(`MCP_SCHEMA_DOCUMENT_NOT_FOUND: ${documentName}`)
  const pointer = rawFragment ? decodeURIComponent(rawFragment) : ''
  return { documentName, pointer, value: resolvePointer(document, pointer) }
}

function dereference(value: unknown, currentDocument: string, stack: Set<string>): JsonValue {
  if (value === null || typeof value === 'boolean' || typeof value === 'number' || typeof value === 'string') return value
  if (Array.isArray(value)) return value.map((item) => dereference(item, currentDocument, new Set(stack)))
  if (!value || typeof value !== 'object') throw new Error('MCP_SCHEMA_VALUE_INVALID')

  const source = value as JsonObject
  if (typeof source.$ref === 'string') {
    const resolved = resolveReference(source.$ref, currentDocument)
    const key = `${resolved.documentName}#${resolved.pointer}`
    if (stack.has(key)) throw new Error(`MCP_SCHEMA_REFERENCE_CYCLE: ${key}`)
    const nextStack = new Set(stack)
    nextStack.add(key)
    const target = dereference(resolved.value, resolved.documentName, nextStack)
    const siblings = Object.fromEntries(Object.entries(source).filter(([keyName]) => keyName !== '$ref'))
    if (Object.keys(siblings).length === 0) return target
    return {
      allOf: [target],
      ...dereference(siblings, currentDocument, new Set(stack)) as JsonObject,
    }
  }

  return Object.fromEntries(
    Object.entries(source).map(([key, item]) => [key, dereference(item, currentDocument, new Set(stack))]),
  ) as JsonObject
}

function schemaFromRef(reference: string): JsonObject {
  const resolved = resolveReference(reference, 'mcp-tool-contracts.schema.json')
  const schema = dereference(resolved.value, resolved.documentName, new Set())
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) throw new Error(`MCP_TOOL_SCHEMA_INVALID: ${reference}`)
  return schema as JsonObject
}

export function getToolInputJsonSchema(definition: McpToolDefinition): JsonObject {
  return schemaFromRef(definition.input_schema_ref)
}

export function getToolOutputJsonSchema(definition: McpToolDefinition): JsonObject {
  return schemaFromRef(definition.output_schema_ref)
}
