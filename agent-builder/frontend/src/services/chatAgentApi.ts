/**
 * Chat Agent API Service
 * ======================
 *
 * CRUD for Chat Agents (Gateway Virtual Servers) and
 * listing available Gateway tools/prompts/resources.
 */

const BASE = '/api/v2/chat-agents';

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const err = await response.text();
    throw new Error(err || response.statusText);
  }
  return response.json();
}

// =============================================================================
// TYPES
// =============================================================================

export type ChatAgentStatus = 'draft' | 'deploying' | 'active' | 'inactive' | 'error';

export interface ChatAgentSummary {
  id: string;
  name: string;
  description: string;
  status: ChatAgentStatus;
  tenant_id: string;
  workspace_id?: string;
  gateway_server_id?: string;
  tool_count: number;
  prompt_count: number;
  resource_count: number;
  created_at?: string;
}

export interface ChatAgentDetail {
  id: string;
  name: string;
  description: string;
  status: ChatAgentStatus;
  tenant_id: string;
  workspace_id?: string;
  gateway_server_id?: string;
  system_prompt?: string;
  associated_tools: string[];
  associated_prompts: string[];
  associated_resources: string[];
  kb_resource_id?: string;
  tags: string[];
  config: Record<string, unknown>;
  mcp_endpoint?: string;
  sse_endpoint?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ChatAgentCreatePayload {
  name: string;
  description?: string;
  tenant_id?: string;
  workspace_id?: string;
  system_prompt?: string;
  associated_tool_ids?: string[];
  associated_prompt_ids?: string[];
  associated_resource_ids?: string[];
  kb_resource_id?: string;
  tags?: string[];
  config?: Record<string, unknown>;
}

export interface ChatAgentUpdatePayload {
  name?: string;
  description?: string;
  system_prompt?: string;
  associated_tool_ids?: string[];
  associated_prompt_ids?: string[];
  associated_resource_ids?: string[];
  kb_resource_id?: string;
  tags?: string[];
  config?: Record<string, unknown>;
}

export interface GatewayItem {
  id: string;
  name: string;
  description?: string;
  [key: string]: unknown;
}

// =============================================================================
// CRUD
// =============================================================================

export async function createChatAgent(payload: ChatAgentCreatePayload) {
  return api<{ id: string; name: string; message: string }>('', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function listChatAgents(tenantId = 'default', status?: string) {
  const params = new URLSearchParams({ tenant_id: tenantId });
  if (status) params.set('status', status);
  return api<{ agents: ChatAgentSummary[]; count: number }>(`?${params}`);
}

export async function getChatAgent(agentId: string) {
  return api<ChatAgentDetail>(`/${agentId}`);
}

export async function updateChatAgent(agentId: string, payload: ChatAgentUpdatePayload) {
  return api<{ id: string; message: string }>(`/${agentId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function deleteChatAgent(agentId: string) {
  return api<{ message: string }>(`/${agentId}`, { method: 'DELETE' });
}

// =============================================================================
// DEPLOY / UNDEPLOY
// =============================================================================

export async function deployChatAgent(agentId: string, activate = true) {
  return api<{
    agent_id: string;
    gateway_server_id: string;
    mcp_endpoint: string;
    sse_endpoint?: string;
    message: string;
  }>(`/${agentId}/deploy`, {
    method: 'POST',
    body: JSON.stringify({ activate }),
  });
}

export async function undeployChatAgent(agentId: string) {
  return api<{ agent_id: string; message: string }>(`/${agentId}/undeploy`, {
    method: 'POST',
  });
}

// =============================================================================
// GATEWAY LISTINGS
// =============================================================================

export async function listGatewayTools() {
  return api<{ tools: GatewayItem[]; count: number }>('/gateway/tools');
}

export async function listGatewayPrompts(includeInactive = false) {
  const params = includeInactive ? '?include_inactive=true' : '';
  return api<{ prompts: GatewayItem[]; count: number }>(`/gateway/prompts${params}`);
}

export async function listGatewayResources(includeInactive = false) {
  const params = includeInactive ? '?include_inactive=true' : '';
  return api<{ resources: GatewayItem[]; count: number }>(`/gateway/resources${params}`);
}

export async function listGatewayServers() {
  return api<{ servers: GatewayItem[]; count: number }>('/gateway/servers');
}

// =============================================================================
// SERVER-SPECIFIC
// =============================================================================

export async function getAgentServerTools(agentId: string) {
  return api<{ tools: GatewayItem[]; count?: number }>(`/${agentId}/server-tools`);
}

export async function getAgentServerPrompts(agentId: string) {
  return api<{ prompts: GatewayItem[]; count?: number }>(`/${agentId}/server-prompts`);
}

export async function getAgentServerResources(agentId: string) {
  return api<{ resources: GatewayItem[]; count?: number }>(`/${agentId}/server-resources`);
}

export async function getAgentMcpConfig(agentId: string) {
  return api<Record<string, unknown>>(`/${agentId}/mcp-config`);
}

// =============================================================================
// LLM CHAT
// =============================================================================

export interface ChatConnectResponse {
  user_id: string;
  agent_id: string;
  message?: string;
  [key: string]: unknown;
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'tool';
  content: string;
  tool_calls?: Array<{ name: string; args: Record<string, unknown>; result?: string }>;
}

export async function connectChat(
  agentId: string,
  model: string = 'gpt-4o',
  temperature: number = 0.7,
  maxTokens: number = 4096,
): Promise<ChatConnectResponse> {
  return api<ChatConnectResponse>(`/${agentId}/chat/connect`, {
    method: 'POST',
    body: JSON.stringify({
      model,
      temperature,
      max_tokens: maxTokens,
      streaming: true,
    }),
  });
}

export async function sendChatMessage(
  agentId: string,
  userId: string,
  message: string,
  onChunk: (data: string) => void,
  onDone: () => void,
  onError: (err: Error) => void,
): Promise<AbortController> {
  const controller = new AbortController();
  const params = new URLSearchParams({ user_id: userId });

  fetch(`${BASE}/${agentId}/chat?${params}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, streaming: true }),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || res.statusText);
      }
      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            onChunk(line.slice(6));
          }
        }
      }
      if (buffer.startsWith('data: ')) {
        onChunk(buffer.slice(6));
      }
      onDone();
    })
    .catch((err) => {
      if (err.name !== 'AbortError') onError(err);
    });

  return controller;
}

export async function sendChatMessageSync(
  agentId: string,
  userId: string,
  message: string,
): Promise<Record<string, unknown>> {
  const params = new URLSearchParams({ user_id: userId });
  return api<Record<string, unknown>>(`/${agentId}/chat?${params}`, {
    method: 'POST',
    body: JSON.stringify({ message, streaming: false }),
  });
}

export async function disconnectChat(agentId: string, userId: string) {
  const params = new URLSearchParams({ user_id: userId });
  return api<Record<string, unknown>>(`/${agentId}/chat/disconnect?${params}`, {
    method: 'POST',
  });
}

export async function chatStatus(agentId: string, userId: string) {
  const params = new URLSearchParams({ user_id: userId });
  return api<{ connected: boolean; error?: string }>(`/${agentId}/chat/status?${params}`);
}
