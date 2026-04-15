import axios from 'axios';

const agentBuilderUrl = (): string => {
  if (import.meta.env.VITE_AGENT_BUILDER_URL) {
    const u = import.meta.env.VITE_AGENT_BUILDER_URL as string;
    return u.endsWith('/') ? u.slice(0, -1) : u;
  }
  return '';
};

const agentApi = axios.create({ baseURL: agentBuilderUrl() });

const PREFIX = '/api/v2/evolving';

// ── Agent CRUD ──────────────────────────────────────────────────

export interface AgentInfo {
  agent_id: string;
  name: string;
  purpose: string;
  domain: string;
  goal: string;
  entity_count: number;
  relationship_count: number;
  is_empty: boolean;
}

export const createAgent = (name: string, purpose: string) =>
  agentApi.post<AgentInfo>(`${PREFIX}/agents`, { name, purpose });

export const listAgents = (limit = 50) =>
  agentApi.get<AgentInfo[]>(`${PREFIX}/agents`, { params: { limit } });

export const getAgent = (agentId: string) =>
  agentApi.get<AgentInfo>(`${PREFIX}/agents/${agentId}`);

export const deleteAgent = (agentId: string) =>
  agentApi.delete(`${PREFIX}/agents/${agentId}`);

// ── Sessions & History ──────────────────────────────────────────

export interface SessionInfo {
  session_id: string;
  thread_id: string;
}

export interface HistoryMessage {
  role: 'user' | 'assistant' | 'tool' | 'tool_call';
  content: string;
  tool_name?: string;
}

export const listSessions = (agentId: string) =>
  agentApi.get<{ sessions: SessionInfo[] }>(`${PREFIX}/agents/${agentId}/sessions`);

export const getChatHistory = (agentId: string, sessionId = '') =>
  agentApi.get<{ session_id: string; messages: HistoryMessage[] }>(
    `${PREFIX}/agents/${agentId}/chat/history`,
    { params: { session_id: sessionId } }
  );

export const resetChat = (agentId: string) =>
  agentApi.post(`${PREFIX}/agents/${agentId}/chat/reset`);

// ── Chat ────────────────────────────────────────────────────────

export interface ChatChunk {
  type: string;
  content?: string;
  session_id?: string;
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  tool_output?: string;
  mode?: 'plan' | 'agent';
  plan?: Plan;
  todos?: Array<{ content: string; status: string }>;
  [key: string]: unknown;
}

export function streamChat(
  agentId: string,
  message: string,
  sessionId: string,
  onChunk: (chunk: ChatChunk) => void,
  onDone: () => void,
  onError: (err: Error) => void
): AbortController {
  const controller = new AbortController();
  const body = JSON.stringify({ message, session_id: sessionId });

  fetch(`${agentBuilderUrl()}${PREFIX}/agents/${agentId}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader');
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data:')) {
            try {
              const parsed = JSON.parse(line.slice(5).trim());
              onChunk(parsed);
            } catch {
              /* skip non-json lines */
            }
          }
        }
      }
      onDone();
    })
    .catch((err) => {
      if (err.name !== 'AbortError') onError(err);
    });

  return controller;
}

// ── Mode & Plan ─────────────────────────────────────────────────

export interface PlanStep {
  id: number;
  content: string;
}

export interface Plan {
  steps: PlanStep[];
  summary: string;
  status: 'draft' | 'approved' | 'executing' | 'completed';
}

export interface ModeInfo {
  agent_id: string;
  mode: 'plan' | 'agent';
  has_plan: boolean;
  plan: Plan | null;
}

export interface PlanResponse {
  agent_id: string;
  plan: Plan | null;
  markdown: string;
}

export const getMode = (agentId: string) =>
  agentApi.get<ModeInfo>(`${PREFIX}/agents/${agentId}/mode`);

export const switchMode = (agentId: string, mode: 'plan' | 'agent') =>
  agentApi.post(`${PREFIX}/agents/${agentId}/mode`, { mode });

export const getPlan = (agentId: string) =>
  agentApi.get<PlanResponse>(`${PREFIX}/agents/${agentId}/plan`);

export const updatePlanStep = (agentId: string, stepId: number, newContent: string) =>
  agentApi.put<PlanResponse>(`${PREFIX}/agents/${agentId}/plan/step`, {
    step_id: stepId,
    new_content: newContent,
  });

export const addPlanStep = (agentId: string, afterStepId: number, content: string) =>
  agentApi.post<PlanResponse>(`${PREFIX}/agents/${agentId}/plan/step`, {
    after_step_id: afterStepId,
    content,
  });

export const removePlanStep = (agentId: string, stepId: number) =>
  agentApi.delete<PlanResponse>(`${PREFIX}/agents/${agentId}/plan/step/${stepId}`);

// ── Ontology ────────────────────────────────────────────────────

export interface OntologyData {
  agent_id: string;
  name: string;
  ontology: {
    domain: string;
    goal: string;
    entity_classes: Array<{
      name: string;
      description: string;
      parent: string;
      properties: Array<{ name: string; type: string; constraint: string; description: string }>;
    }>;
    relationship_predicates: Array<{
      name: string;
      source: string;
      target: string;
      edge_properties: string[];
      description: string;
    }>;
    inference_rules: Array<{ condition: string; inference: string; rule_type: string }>;
    constraints: string[];
  };
}

export const getOntology = (agentId: string) =>
  agentApi.get<OntologyData>(`${PREFIX}/agents/${agentId}/ontology`);

// ── Discoveries ─────────────────────────────────────────────────

export interface Discovery {
  id: string;
  agent_id: string;
  discovery_type: 'entity' | 'relationship';
  name: string;
  sample_count: number;
  first_seen_doc: string;
  sample_properties: string[];
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
  updated_at: string;
}

export const listDiscoveries = (agentId: string, status = '', discoveryType = '') =>
  agentApi.get<{ discoveries: Discovery[] }>(`${PREFIX}/agents/${agentId}/ontology/discoveries`, {
    params: { status, discovery_type: discoveryType },
  });

export const approveDiscovery = (agentId: string, name: string, discoveryType = 'entity') =>
  agentApi.post(`${PREFIX}/agents/${agentId}/ontology/discoveries/${name}/approve`, null, {
    params: { discovery_type: discoveryType },
  });

export const rejectDiscovery = (agentId: string, name: string, discoveryType = 'entity') =>
  agentApi.post(`${PREFIX}/agents/${agentId}/ontology/discoveries/${name}/reject`, null, {
    params: { discovery_type: discoveryType },
  });

// ── Batch ───────────────────────────────────────────────────────

export interface BatchProgress {
  batch_id: string;
  status: string;
  total: number;
  processed: number;
  successful: number;
  failed: number;
  needs_review: number;
  percent_complete: number;
}

export const getBatchProgress = (agentId: string, batchId = '') =>
  agentApi.get<BatchProgress>(`${PREFIX}/agents/${agentId}/batch/progress`, {
    params: { batch_id: batchId },
  });

// ── Notifications ───────────────────────────────────────────────

export interface AgentNotification {
  agent_id: string;
  event_type: string;
  data: Record<string, unknown>;
  timestamp: number;
}

export const getNotifications = (agentId: string, limit = 50) =>
  agentApi.get<{ notifications: AgentNotification[] }>(`${PREFIX}/agents/${agentId}/notifications`, {
    params: { limit },
  });

// ── Upload ──────────────────────────────────────────────────────

export interface UploadResult {
  file_count: number;
  message: string;
  paths?: string[];
}

export const uploadFiles = (agentId: string, files: File[]) => {
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  return agentApi.post<UploadResult>(`${PREFIX}/agents/${agentId}/upload-samples`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

// ── Sources (URL/S3/MinIO) ───────────────────────────────────────

export const addSources = (agentId: string, urls: string[], sourceType = 'url') =>
  agentApi.post<UploadResult>(`${PREFIX}/agents/${agentId}/add-sources`, { urls, source_type: sourceType });

// ── Utilities ───────────────────────────────────────────────────

const PALETTE = [
  '#6366f1', '#8b5cf6', '#ec4899', '#ef4444', '#f97316',
  '#eab308', '#22c55e', '#14b8a6', '#06b6d4', '#3b82f6',
  '#a855f7', '#f43f5e', '#10b981', '#0ea5e9', '#d946ef',
];

export function hashColor(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

export { agentBuilderUrl, PREFIX };
