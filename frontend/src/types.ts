/** 与后端 SSE 契约对应的前端类型 */

export interface IntentResult {
  input: string
  simple_input: string
  module: string
  role: string
  description: string
  intent_type: 'irrelevant' | 'precise' | 'vague'
  missing_fields: string[]
  clarify_question: string
  intent_reason: string
}

/** 检索 step 事件 detail 中的命中条目（混合检索扩展字段为可选，纯稠密链路为 null） */
export interface RetrieveHit {
  chunk_id: string
  doc: string
  path: string
  score: number
  dense_score?: number | null
  sparse_score?: number | null
  fused_score?: number | null
  rerank_score?: number | null
  sparse_rank?: number | null
}

export interface StepEvent {
  type: 'intent' | 'retrieve' | 'thinking' | 'tool_call'
  title: string
  detail: Record<string, any> & {
    /** intent step 扩展：意图识别模型的思维链（reasoning 模型才有） */
    reasoning?: string
    /** retrieve step 扩展：检索模式与命中列表 */
    mode?: 'dense' | 'hybrid' | 'hybrid-rerank'
    hits?: RetrieveHit[]
  }
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
  reasoning?: string
  /** 意图识别思维链的实时暂存（流式期间展示；「意图识别完成」step 到达后由 steps detail 接管并清空） */
  intentReasoning?: string
  clarify?: ClarifyPayload
  streaming?: boolean
  feedback?: 1 | -1 | 0
}

export interface SessionItem {
  id: string
  title: string
  updated_at: string
}

/** 管理端类型 */

export interface DocumentItem {
  id: string
  doc_name: string
  file_size: number
  status: 'pending' | 'processing' | 'success' | 'failure'
  error_message?: string | null
  chunk_count: number
  created_at: string | null
}

export interface ChunkItem {
  id: string
  chunk_type: 'parent' | 'child'
  parent_id: string | null
  child_index: number
  path: string
  level: number
  chunk_index: number
  content: string
  char_count: number
  has_images: boolean
  image_count: number
  vector_state: string | null
  vector_id: string | null
}

export interface PromptItem {
  key: string
  name: string
  content: string
  is_customized: boolean
}

export interface AdminSessionItem {
  id: string
  title: string
  doc_filter: string | null
  created_at: string | null
  updated_at: string | null
}

export interface AdminSessionDetail {
  session: {
    id: string
    title: string
    doc_filter: string | null
    clarify_state: unknown
    created_at: string | null
    updated_at: string | null
  }
  messages: {
    id: string
    role: 'user' | 'assistant'
    content: string
    sources: SourceChunk[]
    steps: StepEvent[]
    reasoning: string | null
    created_at: string | null
  }[]
}

/** 检索日志阶段 */
export type RetrievalStage = 'dense' | 'sparse' | 'fused' | 'final'

export interface SessionLogs {
  intent_logs: {
    id: number
    message_id: string
    sub_question: Record<string, any>
    intent_type: string
    intent_reason: string
    used_llm: boolean
    created_at: string | null
  }[]
  retrieval_logs: {
    id: number
    message_id: string
    sub_question: string
    chunk_id: string
    doc: string
    path: string
    score: number
    dense_score: number | null
    sparse_score: number | null
    fused_score: number | null
    rerank_score: number | null
    mode: 'dense' | 'hybrid' | 'hybrid-rerank'
    stage: RetrievalStage
    hit_rank: number
    created_at: string | null
  }[]
  llm_call_logs: LlmCallLog[]
}

export interface LlmCallLog {
  id: number
  message_id: string
  call_type: 'intent' | 'answer'
  system_prompt: string
  messages: { role: string; content: string }[]
  output: string
  created_at: string | null
}

/** 用户反馈（管理端列表条目，含 join 出的会话信息） */

export interface FeedbackItem {
  id: number
  score: 1 | -1
  comment: string
  created_at: string | null
  message_id: string
  session_id: string | null
  session_title: string | null
  message_content: string | null
}

export type TicketStatus = 'pending' | 'processing' | 'resolved' | 'closed'
export type TicketPriority = 'high' | 'medium' | 'low'

export interface TicketItem {
  id: number
  title: string
  status: TicketStatus
  priority: TicketPriority
  result: string | null
  processed_at: string | null
  created_at: string | null
  updated_at: string | null
  feedback_count: number
}

export interface TicketDetail {
  ticket: Omit<TicketItem, 'feedback_count'>
  feedbacks: FeedbackItem[]
}
