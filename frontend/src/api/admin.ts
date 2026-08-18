/** 管理端 API 封装（复用 fetch 模式，BASE 为空走 vite/nginx 代理） */
import type {
  AdminSessionDetail,
  AdminSessionItem,
  ChunkItem,
  DocumentItem,
  FeedbackItem,
  PromptItem,
  SessionLogs,
  TicketDetail,
  TicketItem,
  TicketPriority,
  TicketStatus,
} from '../types'

const BASE = '/admin/api'

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`)
  if (!resp.ok) throw new Error(`请求失败：${resp.status}`)
  return resp.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) throw new Error(`请求失败：${resp.status}`)
  return resp.json()
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(`请求失败：${resp.status}`)
  return resp.json()
}

async function del(path: string): Promise<void> {
  const resp = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  if (!resp.ok) throw new Error(`请求失败：${resp.status}`)
}

// ---------- 知识库 ----------

export async function uploadDocument(file: File): Promise<DocumentItem> {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(`${BASE}/documents`, { method: 'POST', body: form })
  if (!resp.ok) throw new Error(`上传失败：${resp.status}`)
  return resp.json()
}

export function listDocuments(
  offset = 0,
  limit = 50,
): Promise<{ documents: DocumentItem[]; total: number }> {
  return get(`/documents?offset=${offset}&limit=${limit}`)
}

export function getDocument(id: string): Promise<DocumentItem> {
  return get(`/documents/${id}`)
}

export function previewUrl(id: string): string {
  return `${BASE}/documents/${id}/preview`
}

export function getMd(id: string): Promise<{ content: string }> {
  return get(`/documents/${id}/md`)
}

export function listChunks(id: string): Promise<{ chunks: ChunkItem[] }> {
  return get(`/documents/${id}/chunks`)
}

export function reprocessDocument(id: string): Promise<{ status: string }> {
  return post(`/documents/${id}/reprocess`)
}

export function deleteDocument(id: string): Promise<void> {
  return del(`/documents/${id}`)
}

// ---------- 提示词 ----------

export function listPrompts(): Promise<{ prompts: PromptItem[] }> {
  return get('/prompts')
}

export function updatePrompt(key: string, content: string): Promise<{ status: string }> {
  return put(`/prompts/${key}`, { content })
}

// ---------- 会话 ----------

export function listAdminSessions(
  offset = 0,
  limit = 50,
): Promise<{ sessions: AdminSessionItem[]; total: number }> {
  return get(`/sessions?offset=${offset}&limit=${limit}`)
}

export function getAdminSession(id: string): Promise<AdminSessionDetail> {
  return get(`/sessions/${id}`)
}

export function getSessionLogs(id: string): Promise<SessionLogs> {
  return get(`/sessions/${id}/logs`)
}

// ---------- 用户反馈 ----------

export function listAdminFeedbacks(
  offset = 0,
  limit = 50,
): Promise<{ feedbacks: FeedbackItem[]; total: number }> {
  return get(`/feedbacks?offset=${offset}&limit=${limit}`)
}

export function listAdminTickets(
  offset = 0,
  limit = 50,
): Promise<{ tickets: TicketItem[]; total: number }> {
  return get(`/tickets?offset=${offset}&limit=${limit}`)
}

export function getAdminTicket(id: number): Promise<TicketDetail> {
  return get(`/tickets/${id}`)
}

export function createTicket(body: {
  title: string
  priority: TicketPriority
  feedback_ids: number[]
}): Promise<{ ticket: TicketItem }> {
  return post('/tickets', body)
}

export function updateTicket(
  id: number,
  body: { title?: string; status?: TicketStatus; priority?: TicketPriority; result?: string },
): Promise<{ status: string }> {
  return put(`/tickets/${id}`, body)
}

export function linkFeedbacksToTicket(
  id: number,
  feedbackIds: number[],
): Promise<{ status: string; linked: number }> {
  return post(`/tickets/${id}/feedbacks`, { feedback_ids: feedbackIds })
}

export function unlinkFeedbackFromTicket(
  ticketId: number,
  feedbackId: number,
): Promise<void> {
  return del(`/tickets/${ticketId}/feedbacks/${feedbackId}`)
}
