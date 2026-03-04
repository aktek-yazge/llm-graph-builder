/**
 * Agent Builder API Service
 * =========================
 *
 * Agent Builder backend API ile iletişim.
 * REST API çağrıları ve type definitions.
 */

// =============================================================================
// TYPES
// =============================================================================

export interface BuilderSession {
  id: string;
  tenant_id: string;
  user_id?: string;
  status: 'active' | 'completed' | 'abandoned';
  current_state: string;
  state_data: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface BuilderChatResponse {
  message: string;
  state: string;
  state_data?: Record<string, unknown>;
  action_required?: string;
  options?: Array<{ id: string; label: string }>;
}

export interface Goal {
  id: string;
  name: string;
  description: string;
  goal_type: string;
  status?: string;
  tenant_id?: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  skill_category: string;
  effectiveness_score: number;
  is_global: boolean;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  purpose: string;
  status: 'draft' | 'active' | 'archived';
  tenant_id: string;
  mcp_virtual_server_id?: string;
  deployed_at?: string;
}

export interface SchemaProposal {
  entities: Array<{
    entity_type: string;
    description: string;
    properties: Record<string, unknown>;
    is_new: boolean;
  }>;
  relationships: Array<{
    relationship_type: string;
    source_entity: string;
    target_entity: string;
    description: string;
  }>;
  confidence: number;
  reasoning: string;
}

export interface RichMessagePart {
  type: 'text' | 'upload_zone' | 'action_buttons' | 'card' | 'suggestion' | 'progress' | 'table' | 'status' | 'trigger_side_upload';
  content: string;
  metadata: Record<string, unknown>;
}

export interface AgentChatResult {
  response: string;
  session_id: string;
  agent_id: string;
  rich_parts: RichMessagePart[];
  phase?: string;
}

// =============================================================================
// API BASE
// =============================================================================

const API_BASE = '/api/v2/agent-builder';

async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;

  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || `API Error: ${response.status}`);
  }

  return response.json();
}

// =============================================================================
// SESSION API
// =============================================================================

export const agentBuilderApi = {
  /**
   * Yeni session oluştur
   */
  async createSession(tenantId: string): Promise<BuilderSession> {
    return apiRequest<BuilderSession>('/sessions', {
      method: 'POST',
      body: JSON.stringify({ tenant_id: tenantId }),
    });
  },

  /**
   * Session bilgilerini getir
   */
  async getSession(sessionId: string): Promise<BuilderSession> {
    return apiRequest<BuilderSession>(`/sessions/${sessionId}`);
  },

  /**
   * Mesaj gönder
   */
  async sendMessage(sessionId: string, message: string): Promise<BuilderChatResponse> {
    return apiRequest<BuilderChatResponse>(`/sessions/${sessionId}/message`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    });
  },

  /**
   * Örnek belgeler yükle
   */
  async uploadSamples(sessionId: string, files: File[]): Promise<{ sample_ids: string[] }> {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));

    const response = await fetch(`${API_BASE}/sessions/${sessionId}/upload-samples`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      throw new Error('Upload failed');
    }

    return response.json();
  },

  // ===========================================================================
  // GOALS API
  // ===========================================================================

  /**
   * Goal listesi
   */
  async listGoals(tenantId: string, params?: { goal_type?: string; context?: string }): Promise<Goal[]> {
    const searchParams = new URLSearchParams({ tenant_id: tenantId });
    if (params?.goal_type) searchParams.append('goal_type', params.goal_type);
    if (params?.context) searchParams.append('context', params.context);

    return apiRequest<Goal[]>(`/goals?${searchParams.toString()}`);
  },

  /**
   * Goal oluştur
   */
  async createGoal(goal: Omit<Goal, 'id'>): Promise<Goal> {
    return apiRequest<Goal>('/goals', {
      method: 'POST',
      body: JSON.stringify(goal),
    });
  },

  /**
   * Goal için skill'leri getir
   */
  async getGoalSkills(goalId: string, tenantId: string): Promise<Skill[]> {
    return apiRequest<Skill[]>(`/goals/${goalId}/skills?tenant_id=${tenantId}`);
  },

  // ===========================================================================
  // SKILLS API
  // ===========================================================================

  /**
   * Skill listesi
   */
  async listSkills(tenantId: string, params?: { category?: string }): Promise<Skill[]> {
    const searchParams = new URLSearchParams({ tenant_id: tenantId });
    if (params?.category) searchParams.append('category', params.category);

    return apiRequest<Skill[]>(`/skills?${searchParams.toString()}`);
  },

  /**
   * Skill detayı
   */
  async getSkill(skillId: string): Promise<Skill> {
    return apiRequest<Skill>(`/skills/${skillId}`);
  },

  /**
   * Skill test et
   */
  async testSkill(skillId: string, documentIds: string[]): Promise<{
    success: boolean;
    extracted_entities: unknown[];
    extracted_relationships: unknown[];
    errors: string[];
    execution_time_ms: number;
  }> {
    return apiRequest(`/skills/${skillId}/test`, {
      method: 'POST',
      body: JSON.stringify({ document_ids: documentIds }),
    });
  },

  // ===========================================================================
  // AGENTS API
  // ===========================================================================

  /**
   * Agent listesi
   */
  async listAgents(tenantId: string, params?: { status?: string }): Promise<Agent[]> {
    const searchParams = new URLSearchParams({ tenant_id: tenantId });
    if (params?.status) searchParams.append('status', params.status);

    return apiRequest<Agent[]>(`/agents?${searchParams.toString()}`);
  },

  /**
   * Agent detayı
   */
  async getAgent(agentId: string): Promise<Agent> {
    return apiRequest<Agent>(`/agents/${agentId}`);
  },

  /**
   * Agent oluştur
   */
  async createAgent(agent: {
    name: string;
    description: string;
    purpose: string;
    tenant_id: string;
    goal_ids: string[];
    skill_ids: string[];
  }): Promise<Agent> {
    return apiRequest<Agent>('/agents', {
      method: 'POST',
      body: JSON.stringify(agent),
    });
  },

  /**
   * Agent deploy et
   */
  async deployAgent(agentId: string): Promise<{
    success: boolean;
    mcp_virtual_server_id?: string;
    endpoint_url?: string;
    message: string;
  }> {
    return apiRequest(`/agents/${agentId}/deploy`, {
      method: 'POST',
    });
  },

  /**
   * Agent ile belge işle
   */
  async processWithAgent(agentId: string, fileIds: string[]): Promise<{
    task_id: string;
    status: string;
    message: string;
  }> {
    return apiRequest(`/agents/${agentId}/process`, {
      method: 'POST',
      body: JSON.stringify({ file_ids: fileIds }),
    });
  },

  // ===========================================================================
  // ONTOLOGY API
  // ===========================================================================

  /**
   * Schema önerisi al
   */
  async suggestSchema(sampleIds: string[], context: string): Promise<SchemaProposal> {
    const searchParams = new URLSearchParams({ context });
    sampleIds.forEach((id) => searchParams.append('sample_ids', id));

    return apiRequest<SchemaProposal>(`/ontology/suggest-schema?${searchParams.toString()}`, {
      method: 'POST',
    });
  },

  /**
   * Context listesi
   */
  async listContexts(): Promise<Array<{ id: string; name: string; description: string }>> {
    return apiRequest('/ontology/contexts');
  },

  // ===========================================================================
  // HEALTH CHECK
  // ===========================================================================

  /**
   * API sağlık kontrolü
   */
  async healthCheck(): Promise<{ status: string; version: string }> {
    return apiRequest('/health');
  },

  // ===========================================================================
  // AGENT RUNTIME CHAT API
  // ===========================================================================

  async chatWithAgent(
    agentId: string,
    message: string,
    sessionId?: string,
  ): Promise<AgentChatResult> {
    return apiRequest(`/agents/${agentId}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message, session_id: sessionId || null }),
    });
  },

  async getAgentChatHistory(
    agentId: string,
    sessionId: string,
  ): Promise<{ messages: Array<{ role: string; content: string; created_at?: string }>; count: number }> {
    return apiRequest(`/agents/${agentId}/chat/history?session_id=${sessionId}`);
  },
};

export default agentBuilderApi;

// Alias for named import
export const AgentBuilderAPI = {
  ...agentBuilderApi,
  // Additional convenience methods
  listSessions: async (tenantId: string) => {
    return apiRequest<BuilderSession[]>(`/sessions?tenant_id=${tenantId}`);
  },
};
