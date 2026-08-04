/** 与后端 SSE 契约对应的前端类型 */

export interface IntentResult {
  input: string
  simple_input: string
  module: string
  role: string
  description: string
  chunk_id_list: string[]
  intent_type: 'irrelevant' | 'precise' | 'vague'
  intent_reason: string
}

export interface StepEvent {
  type: 'intent' | 'retrieve' | 'thinking' | 'tool_call'
  title: string
  detail: Record<string, any>
}

export interface SourceChunk {
  chunk_id: string
  doc: string
  path: string
  score: number
  content: string
  images: string[]
}

export interface ClarifyPayload {
  question: string
  missing_fields: string[]
  intents: IntentResult[]
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  steps: StepEvent[]
  sources: SourceChunk[]
  clarify?: ClarifyPayload
  streaming?: boolean
  feedback?: 1 | -1 | 0
}

export interface SessionItem {
  id: string
  title: string
  updated_at: string
}
