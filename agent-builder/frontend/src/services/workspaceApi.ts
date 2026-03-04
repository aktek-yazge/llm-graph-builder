/**
 * Workspace API Service
 * =====================
 *
 * Document Processing Workspace backend API ile iletisim.
 */

// =============================================================================
// TYPES
// =============================================================================

export interface WorkspaceSummary {
  id: string;
  name: string;
  status: string;
  document_count: number;
  processed_count: number;
  success_rate: number;
  created_at?: string;
}

export interface Workspace {
  id: string;
  name: string;
  description: string;
  tenant_id: string;
  status: string;
  ocr_mode: string;
  batch_size: number;
  agent_id?: string;
  skill_id?: string;
  document_count: number;
  sample_count: number;
  created_at?: string;
  updated_at?: string;
}

export interface EntitySchema {
  id: string;
  entity_type: string;
  description: string;
  properties: Record<string, unknown>;
}

export interface RelationshipSchema {
  id: string;
  relationship_type: string;
  source_entity: string;
  target_entity: string;
  description?: string;
}

export interface SchemaDiscovery {
  workspace_id: string;
  status: string;
  entity_schemas: EntitySchema[];
  relationship_schemas: RelationshipSchema[];
  summary: string;
  samples_analyzed: number;
}

export interface BatchProgress {
  batch_job_id: string;
  workspace_id: string;
  status: string;
  total: number;
  processed: number;
  successful: number;
  failed: number;
  low_confidence: number;
  percent_complete: number;
  elapsed_seconds: number;
  estimated_remaining_seconds?: number;
}

export interface ReviewItem {
  document_id: string;
  file_name: string;
  status: string;
  confidence_score: number;
  extracted_entities: unknown[];
  extracted_relationships: unknown[];
  error_message?: string;
}

export interface ReviewQueueResponse {
  workspace_id: string;
  total_items: number;
  items: ReviewItem[];
}

export interface WorkspaceStats {
  workspace_id: string;
  name: string;
  status: string;
  total_documents: number;
  successful: number;
  failed: number;
  low_confidence: number;
  in_progress: number;
  average_confidence: number;
  success_rate: number;
}

export interface ElicitationItem {
  id: string;
  doc_id: string;
  file_name: string;
  confidence_score: number;
  status: string;
  node_count: number;
  relationship_count: number;
  extraction_preview: Record<string, unknown>;
}

export interface AgentEvent {
  event_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  source_agent: string;
  workspace_id: string;
  timestamp: string;
}

export interface BlackboardTopic {
  topic: string;
  entry_count: number;
  last_updated: string;
  agents: string[];
}

export interface WorkspaceMonitoring {
  workspace_id: string;
  stats: WorkspaceStats;
  active_batch: BatchProgress | null;
  elicitation: {
    total: number;
    pending: number;
    accepted: number;
    rejected: number;
    recent_items: ElicitationItem[];
  };
  recent_events: AgentEvent[];
  blackboard_topics: BlackboardTopic[];
}

export interface DashboardWorkspace {
  id: string;
  name: string;
  status: string;
  document_count: number;
  processed: number;
  successful: number;
  failed: number;
  low_confidence: number;
  in_progress: number;
  success_rate: number;
  created_at?: string;
}

export interface DashboardAgent {
  id: string;
  name: string;
  status: string;
  deployed: boolean;
}

export interface ActiveBatch {
  batch_job_id: string;
  workspace_id: string;
  status: string;
  total: number;
  processed: number;
  percent_complete: number;
}

export interface DashboardOverview {
  summary: {
    workspace_count: number;
    active_workspaces: number;
    agent_count: number;
    total_documents: number;
    total_processed: number;
    total_successful: number;
    total_failed: number;
    total_low_confidence: number;
    total_in_progress: number;
    overall_success_rate: number;
  };
  workspaces: DashboardWorkspace[];
  agents: DashboardAgent[];
  active_batches: ActiveBatch[];
  recent_events: AgentEvent[];
  health: Record<string, { status: string; error?: string }>;
}

export interface PaginatedWorkspaces {
  items: DashboardWorkspace[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// =============================================================================
// API BASE
// =============================================================================

const API_BASE = '/api/v2/workspaces';

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
// WORKSPACE API
// =============================================================================

export const workspaceApi = {
  async createWorkspace(data: {
    name: string;
    description?: string;
    tenant_id: string;
    ocr_mode?: string;
    batch_size?: number;
  }): Promise<Workspace> {
    return apiRequest<Workspace>('', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  async listWorkspaces(tenantId: string): Promise<WorkspaceSummary[]> {
    return apiRequest<WorkspaceSummary[]>(`?tenant_id=${tenantId}`);
  },

  async getWorkspace(workspaceId: string): Promise<Workspace> {
    return apiRequest<Workspace>(`/${workspaceId}`);
  },

  async uploadSamples(workspaceId: string, files: File[]): Promise<{
    workspace_id: string;
    sample_ids: string[];
    file_count: number;
    message: string;
  }> {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));

    const response = await fetch(`${API_BASE}/${workspaceId}/upload-samples`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) throw new Error('Upload failed');
    return response.json();
  },

  async analyzeSamples(workspaceId: string): Promise<SchemaDiscovery> {
    return apiRequest<SchemaDiscovery>(`/${workspaceId}/analyze-samples`, {
      method: 'POST',
    });
  },

  async getSchema(workspaceId: string): Promise<{
    workspace_id: string;
    entity_schemas: EntitySchema[];
    relationship_schemas: RelationshipSchema[];
  }> {
    return apiRequest(`/${workspaceId}/schema`);
  },

  async approveSchema(workspaceId: string, data: {
    entity_schema_ids: string[];
    relationship_schema_ids: string[];
    approved: boolean;
  }): Promise<{ workspace_id: string; status: string; message: string }> {
    return apiRequest(`/${workspaceId}/approve-schema`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  async uploadBatch(workspaceId: string, files: File[]): Promise<{
    workspace_id: string;
    batch_job_id: string;
    files_uploaded: number;
    message: string;
  }> {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));

    const response = await fetch(`${API_BASE}/${workspaceId}/upload-batch`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) throw new Error('Batch upload failed');
    return response.json();
  },

  async startProcessing(workspaceId: string, batchJobId: string): Promise<{
    batch_job_id: string;
    documents_queued: number;
    batches: number;
    estimated_seconds: number;
  }> {
    return apiRequest(`/${workspaceId}/start-processing?batch_job_id=${batchJobId}`, {
      method: 'POST',
    });
  },

  streamProgress(workspaceId: string, batchJobId: string, onMessage: (data: BatchProgress) => void): EventSource {
    const url = `${API_BASE}/${workspaceId}/progress?batch_job_id=${batchJobId}`;
    const eventSource = new EventSource(url);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as BatchProgress;
        onMessage(data);
      } catch {
        // ignore parse errors
      }
    };

    return eventSource;
  },

  async getProgressSnapshot(workspaceId: string, batchJobId: string): Promise<BatchProgress> {
    return apiRequest(`/${workspaceId}/progress-snapshot?batch_job_id=${batchJobId}`);
  },

  async getReviewQueue(workspaceId: string, limit = 50, offset = 0): Promise<ReviewQueueResponse> {
    return apiRequest(`/${workspaceId}/review-queue?limit=${limit}&offset=${offset}`);
  },

  async approveReviewItems(workspaceId: string, documentIds: string[], action = 'approve'): Promise<{
    action: string;
    count: number;
  }> {
    return apiRequest(`/${workspaceId}/approve-batch?action=${action}`, {
      method: 'POST',
      body: JSON.stringify(documentIds),
    });
  },

  async getStats(workspaceId: string): Promise<WorkspaceStats> {
    return apiRequest(`/${workspaceId}/stats`);
  },

  async getMonitoring(workspaceId: string): Promise<WorkspaceMonitoring> {
    return apiRequest(`/${workspaceId}/monitoring`);
  },

  async getElicitationQueue(workspaceId: string, status = 'pending', limit = 50): Promise<{
    workspace_id: string;
    total: number;
    pending: number;
    accepted: number;
    rejected: number;
    items: ElicitationItem[];
  }> {
    return apiRequest(`/${workspaceId}/elicitation-queue?status=${status}&limit=${limit}`);
  },

  async resolveElicitation(workspaceId: string, requestId: string, action: string, modifiedResult?: string): Promise<{
    id: string;
    action: string;
    doc_id: string;
    status: string;
  }> {
    return apiRequest(`/${workspaceId}/elicitation/${requestId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ action, modified_result: modifiedResult }),
    });
  },
};

const DASHBOARD_BASE = '/api/v2/dashboard';
const COMMS_BASE = '/api/v2/comms';

export const dashboardApi = {
  async getOverview(tenantId = 'default'): Promise<DashboardOverview> {
    const response = await fetch(`${DASHBOARD_BASE}/overview?tenant_id=${tenantId}`, {
      headers: { 'Content-Type': 'application/json' },
    });
    if (!response.ok) throw new Error(`Dashboard API Error: ${response.status}`);
    return response.json();
  },

  async getWorkspacesPaginated(
    tenantId = 'default',
    page = 1,
    pageSize = 10,
  ): Promise<PaginatedWorkspaces> {
    const params = new URLSearchParams({
      tenant_id: tenantId,
      page: String(page),
      page_size: String(pageSize),
    });
    const response = await fetch(`${DASHBOARD_BASE}/workspaces?${params}`, {
      headers: { 'Content-Type': 'application/json' },
    });
    if (!response.ok) throw new Error(`Dashboard API Error: ${response.status}`);
    return response.json();
  },

  subscribeToEvents(
    workspaceId: string,
    onEvent: (event: AgentEvent) => void,
  ): EventSource {
    const url = workspaceId
      ? `${COMMS_BASE}/events/stream?workspace_id=${workspaceId}`
      : `${COMMS_BASE}/events/stream`;

    const es = new EventSource(url);

    es.addEventListener('connected', () => {});

    const knownTypes = [
      'workspace.schema_proposed',
      'workspace.schema_approved',
      'agent.kb_agent_created',
      'processing.started',
      'processing.progress',
      'processing.completed',
      'processing.failed',
      'elicitation.requested',
      'elicitation.resolved',
      'agent.message',
      'blackboard.updated',
    ];

    for (const type of knownTypes) {
      es.addEventListener(type, (e: MessageEvent) => {
        try {
          onEvent(JSON.parse(e.data) as AgentEvent);
        } catch { /* ignore */ }
      });
    }

    es.onmessage = (e: MessageEvent) => {
      try {
        onEvent(JSON.parse(e.data) as AgentEvent);
      } catch { /* ignore */ }
    };

    return es;
  },

  async getEventHistory(workspaceId = '', limit = 50): Promise<{ events: AgentEvent[]; count: number }> {
    const params = new URLSearchParams();
    if (workspaceId) params.set('workspace_id', workspaceId);
    params.set('limit', String(limit));
    const response = await fetch(`${COMMS_BASE}/events/history?${params}`, {
      headers: { 'Content-Type': 'application/json' },
    });
    if (!response.ok) throw new Error(`Comms API Error: ${response.status}`);
    return response.json();
  },

  async getBlackboardTopics(workspaceId = ''): Promise<{ topics: BlackboardTopic[] }> {
    const params = workspaceId ? `?workspace_id=${workspaceId}` : '';
    const response = await fetch(`${COMMS_BASE}/blackboard/topics${params}`, {
      headers: { 'Content-Type': 'application/json' },
    });
    if (!response.ok) throw new Error(`Comms API Error: ${response.status}`);
    return response.json();
  },

  async getBlackboardEntries(topic = '', workspaceId = '', limit = 20): Promise<{
    entries: Array<{
      id: string;
      topic: string;
      content: string;
      agent_id: string;
      entry_type: string;
      created_at: string;
    }>;
    count: number;
  }> {
    const params = new URLSearchParams();
    if (topic) params.set('topic', topic);
    if (workspaceId) params.set('workspace_id', workspaceId);
    params.set('limit', String(limit));
    const response = await fetch(`${COMMS_BASE}/blackboard?${params}`, {
      headers: { 'Content-Type': 'application/json' },
    });
    if (!response.ok) throw new Error(`Comms API Error: ${response.status}`);
    return response.json();
  },
};

export interface ChatStreamCallbacks {
  onChunk?: (text: string) => void;
  onToolCall?: (name: string, args: Record<string, unknown>) => void;
  onToolResult?: (name: string, result: string) => void;
  onDone?: (data: {
    full_response: string;
    session_id: string;
    agent_id: string;
    rich_parts: Array<{ type: string; content: string; metadata: Record<string, unknown> }>;
  }) => void;
  onError?: (error: string) => void;
}

export const workspaceChatApi = {
  async chatStream(
    workspaceId: string,
    message: string,
    sessionId: string | undefined,
    callbacks: ChatStreamCallbacks,
  ): Promise<void> {
    const response = await fetch(`${API_BASE}/${workspaceId}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId || null }),
    });

    if (!response.ok || !response.body) {
      const err = await response.text().catch(() => 'Stream error');
      callbacks.onError?.(err);
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let currentEventType = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEventType = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          const data = line.slice(6);
          try {
            const parsed = JSON.parse(data);
            switch (currentEventType) {
              case 'message':
                callbacks.onChunk?.(parsed.text || '');
                break;
              case 'tool_call':
                callbacks.onToolCall?.(parsed.name || '', parsed.args || {});
                break;
              case 'tool_result':
                callbacks.onToolResult?.(parsed.name || '', parsed.result || '');
                break;
              case 'done':
                callbacks.onDone?.(parsed);
                break;
              case 'error':
                callbacks.onError?.(parsed.error || 'Unknown error');
                break;
            }
          } catch { /* ignore parse errors */ }
          currentEventType = '';
        }
      }
    }
  },

  async chat(
    workspaceId: string,
    message: string,
    sessionId?: string,
  ): Promise<{
    response: string;
    session_id: string;
    agent_id: string;
    rich_parts: Array<{ type: string; content: string; metadata: Record<string, unknown> }>;
    phase?: string;
  }> {
    const response = await fetch(`${API_BASE}/${workspaceId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId || null }),
    });
    if (!response.ok) {
      const err = await response.text();
      throw new Error(err || response.statusText);
    }
    return response.json();
  },

  async getHistory(workspaceId: string): Promise<{
    session_id: string | null;
    messages: Array<{ role: string; content: string; created_at?: string }>;
  }> {
    const response = await fetch(`${API_BASE}/${workspaceId}/chat/history`);
    if (!response.ok) return { session_id: null, messages: [] };
    return response.json();
  },
};

export default workspaceApi;
