import axios from 'axios';

const agentBuilderUrl = (): string => {
  if (import.meta.env.VITE_AGENT_BUILDER_URL) {
    const u = import.meta.env.VITE_AGENT_BUILDER_URL as string;
    return u.endsWith('/') ? u.slice(0, -1) : u;
  }
  return '';
};

const api = axios.create({ baseURL: agentBuilderUrl() });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

const P = '/api/v2/platform';

// ── Models ────────────────────────────────────────────────────────────

export interface LLMModel {
  model_id: string;
  tenant_id: string | null;
  provider: string;
  model_name: string;
  display_name: string | null;
  description: string;
  input_cost_per_1k: number | null;
  output_cost_per_1k: number | null;
  context_window: number | null;
  max_output_tokens: number | null;
  supports_vision: boolean;
  supports_function_calling: boolean;
  finetune_base_model: string | null;
  finetune_status: string | null;
  finetune_config: Record<string, any>;
  is_active: boolean;
  created_at: string | null;
}

export const listModels = (provider?: string) =>
  api.get<{ models: LLMModel[]; total: number }>(P + '/models', { params: provider ? { provider } : {} });

export const createModel = (data: Partial<LLMModel>) =>
  api.post<LLMModel>(P + '/models', data);

export const updateModel = (id: string, data: Partial<LLMModel>) =>
  api.put<LLMModel>(`${P}/models/${id}`, data);

export const deleteModel = (id: string) =>
  api.delete<{ status: string }>(`${P}/models/${id}`);

// ── Evaluations ───────────────────────────────────────────────────────

export interface Evaluation {
  evaluation_id: string;
  tenant_id: string | null;
  agent_id: string | null;
  name: string;
  eval_type: string;
  config: Record<string, any>;
  last_run_at: string | null;
  score: number | null;
  status: string;
  created_at: string | null;
}

export const listEvaluations = () =>
  api.get<{ evaluations: Evaluation[]; total: number }>(P + '/evaluations');

export const createEvaluation = (data: Partial<Evaluation>) =>
  api.post<Evaluation>(P + '/evaluations', data);

export const updateEvaluation = (id: string, data: Partial<Evaluation>) =>
  api.put<Evaluation>(`${P}/evaluations/${id}`, data);

export const deleteEvaluation = (id: string) =>
  api.delete<{ status: string }>(`${P}/evaluations/${id}`);

// ── Guardrails ────────────────────────────────────────────────────────

export interface Guardrail {
  guardrail_id: string;
  tenant_id: string | null;
  name: string;
  guardrail_type: string;
  config: Record<string, any>;
  is_active: boolean;
  scope: string;
  created_at: string | null;
}

export const listGuardrails = () =>
  api.get<{ guardrails: Guardrail[]; total: number }>(P + '/guardrails');

export const createGuardrail = (data: Partial<Guardrail>) =>
  api.post<Guardrail>(P + '/guardrails', data);

export const updateGuardrail = (id: string, data: Partial<Guardrail>) =>
  api.put<Guardrail>(`${P}/guardrails/${id}`, data);

export const deleteGuardrail = (id: string) =>
  api.delete<{ status: string }>(`${P}/guardrails/${id}`);

// ── Tools ─────────────────────────────────────────────────────────────

export interface AgentTool {
  tool_id: string;
  tenant_id: string | null;
  name: string;
  description: string;
  tool_type: string;
  config: Record<string, any>;
  schema_json: Record<string, any>;
  is_active: boolean;
  created_at: string | null;
}

export const listTools = () =>
  api.get<{ tools: AgentTool[]; total: number }>(P + '/tools');

export const createTool = (data: Partial<AgentTool>) =>
  api.post<AgentTool>(P + '/tools', data);

export const updateTool = (id: string, data: Partial<AgentTool>) =>
  api.put<AgentTool>(`${P}/tools/${id}`, data);

export const deleteTool = (id: string) =>
  api.delete<{ status: string }>(`${P}/tools/${id}`);

// ── MCP Gateway (read-only proxy) ────────────────────────────────────

export interface McpTool {
  id: string;
  name: string;
  originalName: string;
  displayName: string;
  description: string;
  originalDescription: string;
  url: string;
  requestType: string;
  integrationType: string;
  inputSchema: Record<string, any>;
  outputSchema: Record<string, any>;
  annotations: Record<string, any>;
  enabled: boolean;
  reachable: boolean;
  gatewayId: string;
  gatewaySlug: string;
  customName: string;
  executionCount: number | null;
  tags: string[];
  createdAt: string;
  updatedAt: string;
  federationSource: string;
  version: number;
}

export interface McpGateway {
  id: string;
  name: string;
  url: string;
  description: string;
  transport: string;
  enabled: boolean;
  reachable: boolean;
  lastSeen: string;
  createdAt: string;
  updatedAt: string;
}

export const listMcpTools = () =>
  api.get<{ tools: McpTool[]; total: number }>(P + '/mcp-tools');

export const listMcpGateways = () =>
  api.get<{ gateways: McpGateway[]; total: number }>(P + '/mcp-gateways');

export const toggleMcpTool = (toolId: string, enabled: boolean) =>
  api.patch(`${P}/mcp-tools/${toolId}/toggle`, null, { params: { enabled } });

// ── Knowledge Bases (graphrag_endpoints) ─────────────────────────────

export interface SourceDocument {
  resource_id: string;
  filename: string;
  size: number;
  content_type: string;
}

export interface KnowledgeBase {
  endpoint_id: string;
  name: string;
  agent_id: string;
  agent_name: string;
  workflow_id: string;
  workflow_name: string;
  workflow_version: number;
  neo4j_uri: string;
  neo4j_database: string;
  ontology_snapshot: Record<string, any>;
  schema_summary: string;
  source_documents: SourceDocument[];
  source_document_count: number;
  entity_type_count: number;
  relationship_type_count: number;
  status: string;
  tenant_id: string | null;
  published_at: string | null;
  created_at: string | null;
}

export const listKnowledgeBases = () =>
  api.get<{ knowledge_bases: KnowledgeBase[]; total: number }>(P + '/knowledge-bases');

export const getKnowledgeBase = (endpointId: string) =>
  api.get<KnowledgeBase>(`${P}/knowledge-bases/${endpointId}`);
