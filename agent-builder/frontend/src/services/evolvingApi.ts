import axios from 'axios';

const agentBuilderUrl = (): string => {
  if (import.meta.env.VITE_AGENT_BUILDER_URL) {
    const u = import.meta.env.VITE_AGENT_BUILDER_URL as string;
    return u.endsWith('/') ? u.slice(0, -1) : u;
  }
  return '';
};

const agentApi = axios.create({ baseURL: agentBuilderUrl() });

agentApi.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

agentApi.interceptors.response.use(
  (res) => res,
  async (error) => {
    const orig = error.config;
    if (error.response?.status === 401 && !orig._retry) {
      orig._retry = true;
      const rt = localStorage.getItem('refresh_token');
      if (rt) {
        try {
          const { data } = await axios.post(
            `${agentBuilderUrl()}/api/v2/auth/refresh`,
            { refresh_token: rt },
          );
          localStorage.setItem('access_token', data.access_token);
          localStorage.setItem('refresh_token', data.refresh_token);
          orig.headers.Authorization = `Bearer ${data.access_token}`;
          return agentApi(orig);
        } catch {
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          window.location.href = '/login';
        }
      }
    }
    return Promise.reject(error);
  },
);

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
  llm_provider?: string | null;
  llm_model?: string | null;
}

export const createAgent = (
  name: string,
  purpose: string,
  llmProvider?: string,
  llmModel?: string,
) =>
  agentApi.post<AgentInfo>(`${PREFIX}/agents`, {
    name,
    purpose,
    ...(llmProvider ? { llm_provider: llmProvider } : {}),
    ...(llmModel ? { llm_model: llmModel } : {}),
  });

export const listAgents = (limit = 50) =>
  agentApi.get<AgentInfo[]>(`${PREFIX}/agents`, { params: { limit } });

export const getAgent = (agentId: string) =>
  agentApi.get<AgentInfo>(`${PREFIX}/agents/${agentId}`);

export const deleteAgent = (agentId: string, purge = false) =>
  agentApi.delete(`${PREFIX}/agents/${agentId}`, { params: { purge } });

export const restoreAgent = (agentId: string) =>
  agentApi.post(`${PREFIX}/agents/${agentId}/restore`);

export const purgeAgent = (agentId: string) =>
  agentApi.delete(`${PREFIX}/agents/${agentId}`, { params: { purge: true } });

export const updateAgentModel = (
  agentId: string,
  llmProvider: string,
  llmModel: string,
) =>
  agentApi.patch<AgentInfo>(`${PREFIX}/agents/${agentId}/model`, {
    llm_provider: llmProvider,
    llm_model: llmModel,
  });

export interface DeletedAgentInfo {
  agent_id: string;
  name: string;
  purpose: string;
  deleted_at: string | null;
}

export const listDeletedAgents = (limit = 200) =>
  agentApi.get<{ deleted: DeletedAgentInfo[]; total: number }>(
    `${PREFIX}/agents/deleted`,
    { params: { limit } }
  );

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

export interface RewindResponse {
  agent_id: string;
  status: string;
  session_id?: string;
  kept_user_messages?: number;
  cutoff_ts?: string | null;
  deleted_records?: number;
  deleted_files?: number;
  deleted_md_paths?: string[];
  ontology_versions_deleted?: number;
}

export const rewindChat = (
  agentId: string,
  sessionId: string,
  userMessageIndex: number,
) =>
  agentApi.post<RewindResponse>(
    `${PREFIX}/agents/${agentId}/chat/rewind`,
    { session_id: sessionId, user_message_index: userMessageIndex },
  );

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

export interface PlanRequestResponse {
  agent_id: string;
  decision: 'approved' | 'rejected';
  mode: 'plan' | 'agent';
  status?: string;
  summary?: string;
}

export const respondPlanModeRequest = (
  agentId: string,
  decision: 'approve' | 'reject',
  reason = '',
  topic = ''
) =>
  agentApi.post<PlanRequestResponse>(`${PREFIX}/agents/${agentId}/mode/plan-request`, {
    decision,
    reason,
    topic,
  });

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

export const clearPlan = (agentId: string) =>
  agentApi.delete<{ agent_id: string; plan: null; mode: 'plan'; status: string }>(
    `${PREFIX}/agents/${agentId}/plan`
  );

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
  id?: number;
  read_at?: string | null;
}

export const getNotifications = (
  agentId: string,
  opts: { limit?: number; sinceId?: number; unreadOnly?: boolean; mode?: 'history' | 'recent' } = {},
) =>
  agentApi.get<{ notifications: AgentNotification[]; count: number }>(
    `${PREFIX}/agents/${agentId}/notifications`,
    {
      params: {
        limit: opts.limit ?? 50,
        ...(opts.sinceId != null ? { since_id: opts.sinceId } : {}),
        ...(opts.unreadOnly ? { unread_only: true } : {}),
        mode: opts.mode ?? 'history',
      },
    },
  );

export const markNotificationsRead = (agentId: string, notificationIds?: number[]) =>
  agentApi.post<{ marked: number }>(`${PREFIX}/agents/${agentId}/notifications/mark-read`, {
    notification_ids: notificationIds ?? null,
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

// ── Ecosystem (aggregated graph view) ───────────────────────────

export interface EcosystemAgent {
  agent_id: string;
  name: string;
  purpose: string;
  domain: string;
  mode: string;
  model: string;
}

export interface EcosystemOntology {
  entity_count: number;
  relationship_count: number;
  rule_count: number;
  constraint_count: number;
  domain: string;
  goal: string;
}

export interface EcosystemDiscoveries {
  pending: number;
  approved: number;
  rejected: number;
}

export interface EcosystemMcp {
  connected: boolean;
  tool_count: number;
  tool_names: string[];
}

export interface EcosystemSubagent {
  name: string;
  description: string;
  tool_count: number;
}

export interface EcosystemBatchLatest {
  batch_id: string;
  status: string;
  total: number;
  processed: number;
  percent_complete: number;
}

export interface EcosystemBatch {
  active_count: number;
  latest: EcosystemBatchLatest | null;
}

export interface EcosystemData {
  agent: EcosystemAgent;
  ontology: EcosystemOntology;
  discoveries: EcosystemDiscoveries;
  mcp: EcosystemMcp;
  celery: { available: boolean };
  neo4j: { configured: boolean; uri: string };
  subagents: EcosystemSubagent[];
  batch: EcosystemBatch;
  resources: { sample_files: number; source_urls: number };
  notifications: { recent_count: number };
}

export const getEcosystem = (agentId: string) =>
  agentApi.get<EcosystemData>(`${PREFIX}/agents/${agentId}/ecosystem`);

// ── Wiki (Sahne) ────────────────────────────────────────────────

export type WikiCategory =
  | 'entities'
  | 'relationships'
  | 'patterns'
  | 'analysis'
  | 'sources'
  | 'general';

export const WIKI_CATEGORY_ORDER: WikiCategory[] = [
  'entities',
  'relationships',
  'patterns',
  'analysis',
  'sources',
  'general',
];

export interface WikiPageSummary {
  path: string;
  category: WikiCategory;
  summary: string;
  version: number;
  links: string[];
  created_at: string | null;
}

export interface WikiPageDetail {
  agent_id: string;
  path: string;
  category: WikiCategory;
  content: string;
  links: string[];
  version: number;
  created_at: string | null;
  backlinks: string[];
  broken_links: string[];
}

export interface WikiListResponse {
  agent_id: string;
  count: number;
  pages: WikiPageSummary[];
}

export interface WikiSearchResponse {
  agent_id: string;
  query: string;
  count: number;
  results: Array<{
    path: string;
    category: WikiCategory;
    summary: string;
    version: number;
  }>;
}

export interface WikiLintReport {
  agent_id: string;
  total_pages: number;
  total_links: number;
  orphan_pages: string[];
  broken_links: Array<{ source: string; target: string }>;
  unresolved_targets: string[];
  issues_count: number;
}

export interface WikiGraphNode {
  id: string;
  path: string;
  category: WikiCategory;
  summary: string;
  backlinks: number;
  outbound: number;
}

export interface WikiGraphEdge {
  source: string;
  target: string;
}

export interface WikiGraphResponse {
  agent_id: string;
  node_count: number;
  edge_count: number;
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
}

export interface WikiLogEntry {
  action: string;
  path: string;
  timestamp: string;
}

export interface WikiHistoryResponse {
  agent_id: string;
  path: string;
  version_count: number;
  versions: Array<{ version: number; source: string; created_at: string | null }>;
}

export const wikiListPages = (agentId: string, category = '') =>
  agentApi.get<WikiListResponse>(`${PREFIX}/agents/${agentId}/wiki/pages`, {
    params: category ? { category } : undefined,
  });

export const wikiGetPage = (agentId: string, path: string) =>
  agentApi.get<WikiPageDetail>(
    `${PREFIX}/agents/${agentId}/wiki/pages/${encodeURI(path)}`
  );

export const wikiSavePage = (agentId: string, path: string, content: string) =>
  agentApi.put<{ path: string; version: number }>(
    `${PREFIX}/agents/${agentId}/wiki/pages/${encodeURI(path)}`,
    { content }
  );

export const wikiDeletePage = (agentId: string, path: string) =>
  agentApi.delete<{ path: string; deleted: boolean }>(
    `${PREFIX}/agents/${agentId}/wiki/pages/${encodeURI(path)}`
  );

export const wikiSearch = (agentId: string, query: string) =>
  agentApi.get<WikiSearchResponse>(`${PREFIX}/agents/${agentId}/wiki/search`, {
    params: { q: query },
  });

export const wikiIndex = (agentId: string) =>
  agentApi.get<{ agent_id: string; markdown: string }>(
    `${PREFIX}/agents/${agentId}/wiki/index`
  );

export const wikiLint = (agentId: string) =>
  agentApi.post<WikiLintReport>(`${PREFIX}/agents/${agentId}/wiki/lint`);

export const wikiTraverse = (agentId: string, start: string, depth = 2) =>
  agentApi.get<{
    agent_id: string;
    start: string;
    depth: number;
    count: number;
    pages: Array<{ path: string; category: WikiCategory; summary: string; links: string[] }>;
  }>(`${PREFIX}/agents/${agentId}/wiki/traverse`, {
    params: { start, depth },
  });

export const wikiLog = (agentId: string, limit = 50) =>
  agentApi.get<{ agent_id: string; count: number; entries: WikiLogEntry[] }>(
    `${PREFIX}/agents/${agentId}/wiki/log`,
    { params: { limit } }
  );

export const wikiGraph = (agentId: string) =>
  agentApi.get<WikiGraphResponse>(`${PREFIX}/agents/${agentId}/wiki/graph`);

export const wikiPageHistory = (agentId: string, path: string) =>
  agentApi.get<WikiHistoryResponse>(
    `${PREFIX}/agents/${agentId}/wiki/history/${encodeURI(path)}`
  );

// ── Scene (publishing frozen snapshot) ──────────────────────────

export interface SceneStats {
  agent_id: string;
  filter_categories: string[];
  page_count_total: number;
  page_count_by_category: Record<string, number>;
  char_count: number;
  token_estimate: number;
  ontology_empty: boolean;
}

export interface ScenePreview {
  agent_id: string;
  filter_categories: string[];
  prompt: string;
  char_count: number;
  token_estimate: number;
  ontology_empty: boolean;
}

export interface SceneMetadata {
  categories: string[];
  page_count: number;
  token_estimate: number;
  frozen_at: string;
}

export interface ScenePublished {
  agent_id: string;
  published: boolean;
  prompt: string;
  char_count?: number;
  token_estimate?: number;
  skill_id?: string | null;
  version?: number | null;
  metadata?: SceneMetadata | null;
  created_at?: string | null;
}

export interface ScenePublishResult {
  agent_id: string;
  published: boolean;
  skill_id?: string | null;
  version?: number | null;
  metadata?: SceneMetadata | null;
}

const joinCategories = (cats?: string[]) => (cats && cats.length ? cats.join(',') : undefined);

export const getSceneStats = (agentId: string, categories?: string[]) =>
  agentApi.get<SceneStats>(`${PREFIX}/agents/${agentId}/scene/stats`, {
    params: { categories: joinCategories(categories) },
  });

export const getScenePreview = (agentId: string, categories?: string[]) =>
  agentApi.get<ScenePreview>(`${PREFIX}/agents/${agentId}/scene/preview`, {
    params: { categories: joinCategories(categories) },
  });

export const getScenePublished = (agentId: string) =>
  agentApi.get<ScenePublished>(`${PREFIX}/agents/${agentId}/scene/published`);

export const publishScene = (agentId: string, categories: string[] = []) =>
  agentApi.post<ScenePublishResult>(`${PREFIX}/agents/${agentId}/scene/publish`, {
    categories,
  });

// ── OCR Document Preview ────────────────────────────────────────

export interface OcrDocumentMeta {
  doc_key: string;
  file_name: string;
  page_count: number;
  total_chars: number;
  duration_ms?: number;
  token_usage?: {
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
    cost_usd?: number;
  } | null;
}

export interface OcrDocumentsResponse {
  agent_id: string;
  count: number;
  documents: OcrDocumentMeta[];
}

export interface OcrPagesResponse {
  doc_key: string;
  file_name: string;
  total_pages: number;
  offset: number;
  limit: number;
  returned: number;
  has_more: boolean;
  pages: string[];
}

export const getOcrDocuments = (agentId: string) =>
  agentApi.get<OcrDocumentsResponse>(`${PREFIX}/agents/${agentId}/ocr/documents`);

export const getOcrPages = (agentId: string, docKey: string, offset = 0, limit = 5) =>
  agentApi.get<OcrPagesResponse>(`${PREFIX}/agents/${agentId}/ocr/${docKey}/pages`, {
    params: { offset, limit },
  });

// ── Unified Resources ───────────────────────────────────────────

export interface ResourceFile {
  resource_id: string;
  filename: string;
  content_type: string;
  size: number;
  ocr_status: 'pending' | 'completed';
  doc_key: string | null;
  page_count: number | null;
  total_chars: number | null;
}

export interface ResourcesResponse {
  resources: ResourceFile[];
  total: number;
}

export const getResources = (agentId: string) =>
  agentApi.get<ResourcesResponse>(`${PREFIX}/agents/${agentId}/resources`);

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

// ── Workflow ─────────────────────────────────────────────────────

export interface WorkflowNodeType {
  type_id: string;
  label: string;
  description: string;
  category: string;
  icon: string;
  color: string;
  params_schema: Record<string, unknown>;
  input_ports: Array<{ name: string; direction: string; data_type: string; required: boolean }>;
  output_ports: Array<{ name: string; direction: string; data_type: string; required: boolean }>;
}

export interface WorkflowDSL {
  nodes: Array<{
    id: string;
    type: string;
    label: string;
    params: Record<string, unknown>;
    position: { x: number; y: number };
  }>;
  edges: Array<{
    from_node: string;
    from_port: string;
    to_node: string;
    to_port: string;
    condition?: string;
  }>;
}

export interface WorkflowData {
  workflow_id: string;
  agent_id: string;
  name: string;
  description: string;
  version: number;
  status: string;
  node_count: number;
  is_template: boolean;
  source_template_id?: string;
  dsl: WorkflowDSL;
  created_at?: string;
  updated_at?: string;
}

export interface WorkflowListItem {
  workflow_id: string;
  name: string;
  description: string;
  version: number;
  status: string;
  node_count: number;
  is_template: boolean;
  source_template_id?: string;
  created_at?: string;
  updated_at?: string;
}

export interface WorkflowTemplate {
  workflow_id: string;
  agent_id: string;
  agent_name: string;
  name: string;
  description: string;
  node_count: number;
  version: number;
  status: string;
}

export interface WorkflowRunSummary {
  run_id: string;
  status: string;
  elapsed_sec?: number;
  node_count?: number;
  errors?: string[];
}

// ── Node types ────────────────────────────────────────────────

export const getWorkflowNodeTypes = () =>
  agentApi.get<{ node_types: WorkflowNodeType[] }>(`${PREFIX}/workflow/node-types`);

// ── Multi-workflow CRUD ───────────────────────────────────────

export const listWorkflows = (agentId: string) =>
  agentApi.get<{ workflows: WorkflowListItem[]; active_workflow_id: string | null }>(
    `${PREFIX}/agents/${agentId}/workflows`,
  );

export const createWorkflowApi = (agentId: string, name: string, description = '') =>
  agentApi.post<WorkflowData>(`${PREFIX}/agents/${agentId}/workflows`, { name, description });

export const getWorkflowById = (agentId: string, workflowId: string) =>
  agentApi.get<WorkflowData>(`${PREFIX}/agents/${agentId}/workflows/${workflowId}`);

export const updateWorkflowById = (agentId: string, workflowId: string, dsl: WorkflowDSL) =>
  agentApi.put<{ workflow_id: string; status: string; node_count: number }>(
    `${PREFIX}/agents/${agentId}/workflows/${workflowId}`,
    { dsl },
  );

export const renameWorkflowApi = (agentId: string, workflowId: string, name: string) =>
  agentApi.patch<{ workflow_id: string; name: string }>(
    `${PREFIX}/agents/${agentId}/workflows/${workflowId}`,
    { name },
  );

export const deleteWorkflowApi = (agentId: string, workflowId: string) =>
  agentApi.delete(`${PREFIX}/agents/${agentId}/workflows/${workflowId}`);

export const setActiveWorkflow = (agentId: string, workflowId: string) =>
  agentApi.put<{ active_workflow_id: string }>(
    `${PREFIX}/agents/${agentId}/active-workflow`,
    { workflow_id: workflowId },
  );

// ── Workflow runs (addressed by workflow_id) ──────────────────

export const startWorkflowRunById = (agentId: string, workflowId: string, mode: string, inputs?: Record<string, unknown>) =>
  agentApi.post<WorkflowRunSummary>(`${PREFIX}/agents/${agentId}/workflows/${workflowId}/runs`, { mode, inputs });

export const publishWorkflowById = (agentId: string, workflowId: string) =>
  agentApi.post<{ workflow_id: string; version: number; status: string }>(
    `${PREFIX}/agents/${agentId}/workflows/${workflowId}/publish`,
  );

// ── Global workflows list ─────────────────────────────────────

export interface GlobalWorkflowItem {
  workflow_id: string;
  agent_id: string | null;
  agent_name: string;
  name: string;
  description: string;
  version: number;
  status: string;
  node_count: number;
  is_template: boolean;
  source_template_id?: string;
  created_at?: string;
  updated_at?: string;
}

export const listAllWorkflows = (params?: { search?: string; status?: string; limit?: number; offset?: number }) =>
  agentApi.get<{ workflows: GlobalWorkflowItem[]; total: number }>(
    `${PREFIX}/workflows`,
    { params },
  );

export const createGlobalWorkflow = (name: string, description = '', agentId?: string) =>
  agentApi.post<WorkflowData>(`${PREFIX}/workflows`, {
    name,
    description,
    agent_id: agentId || null,
  });

export const deleteGlobalWorkflow = (workflowId: string) =>
  agentApi.delete(`${PREFIX}/workflows/${workflowId}`);

// ── Template sharing ─────────────────────────────────────────

export const listWorkflowTemplates = (excludeAgentId?: string) =>
  agentApi.get<{ templates: WorkflowTemplate[] }>(
    `${PREFIX}/workflow-templates`,
    { params: excludeAgentId ? { exclude_agent_id: excludeAgentId } : {} },
  );

export const shareWorkflowAsTemplate = (agentId: string, workflowId: string, description = '') =>
  agentApi.post<{ workflow_id: string; is_template: boolean }>(
    `${PREFIX}/agents/${agentId}/workflows/${workflowId}/share`,
    { description },
  );

export const importWorkflowTemplate = (agentId: string, templateWorkflowId: string, name?: string) =>
  agentApi.post<WorkflowData>(
    `${PREFIX}/agents/${agentId}/import-template`,
    { template_workflow_id: templateWorkflowId, name },
  );

// ── Compat: old singular endpoints (still used by Canvas for backward compat) ──

export const getWorkflow = (agentId: string) =>
  agentApi.get<WorkflowData>(`${PREFIX}/agents/${agentId}/workflow`);

export const updateWorkflow = (agentId: string, dsl: WorkflowDSL) =>
  agentApi.put<{ workflow_id: string; status: string; node_count: number }>(
    `${PREFIX}/agents/${agentId}/workflow`,
    { dsl },
  );

export const startWorkflowRun = (agentId: string, mode: string, inputs?: Record<string, unknown>) =>
  agentApi.post<WorkflowRunSummary>(`${PREFIX}/agents/${agentId}/workflow/runs`, { mode, inputs });

export const listWorkflowRuns = (agentId: string, limit = 20, offset = 0) =>
  agentApi.get<{ runs: WorkflowRunSummary[]; total: number }>(
    `${PREFIX}/agents/${agentId}/workflow/runs`,
    { params: { limit, offset } },
  );

export const getWorkflowRun = (runId: string) =>
  agentApi.get<{ run: WorkflowRunSummary; steps: unknown[] }>(`${PREFIX}/workflow/runs/${runId}`);

export const publishWorkflow = (agentId: string) =>
  agentApi.post<{ workflow_id: string; version: number; status: string }>(
    `${PREFIX}/agents/${agentId}/workflow/publish`,
  );

// ── Global Resources ─────────────────────────────────────────────

export interface GlobalResourceItem {
  resource_id: string;
  agent_id: string;
  agent_name: string;
  type: string;
  filename: string;
  content_type: string;
  size: number;
  path: string;
  created_at?: string;
}

export const listGlobalResources = () =>
  agentApi.get<{ resources: GlobalResourceItem[]; total: number }>(
    `${PREFIX}/resources`,
  );

export const uploadGlobalResource = (agentId: string, files: File[]) => {
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  return agentApi.post<{ file_count: number; message: string; paths: string[] }>(
    `${PREFIX}/resources/upload`,
    form,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      params: { agent_id: agentId },
    },
  );
};

export const deleteGlobalResource = (resourceId: string) =>
  agentApi.delete<{ status: string; resource_id: string }>(
    `${PREFIX}/resources/${resourceId}`,
  );
