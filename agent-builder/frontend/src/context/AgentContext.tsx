import React, { createContext, useContext, useEffect, useState, useCallback, useMemo, useRef, type ReactNode } from 'react';
import {
  type AgentInfo,
  type OntologyData,
  type Discovery,
  type ChatChunk,
  type BatchProgress,
  type Plan,
  type PlanStep,
  type DeletedAgentInfo,
  type WorkflowListItem,
  listAgents,
  createAgent as apiCreateAgent,
  deleteAgent as apiDeleteAgent,
  restoreAgent as apiRestoreAgent,
  purgeAgent as apiPurgeAgent,
  listDeletedAgents as apiListDeletedAgents,
  getOntology,
  listDiscoveries,
  approveDiscovery as apiApprove,
  rejectDiscovery as apiReject,
  streamChat,
  getBatchProgress as apiBatchProgress,
  uploadFiles as apiUploadFiles,
  listSessions,
  getChatHistory,
  getAgent as apiGetAgent,
  resetChat as apiResetChat,
  rewindChat as apiRewindChat,
  getMode as apiGetMode,
  switchMode as apiSwitchMode,
  getPlan as apiGetPlan,
  updatePlanStep as apiUpdatePlanStep,
  addPlanStep as apiAddPlanStep,
  removePlanStep as apiRemovePlanStep,
  clearPlan as apiClearPlan,
  respondPlanModeRequest as apiRespondPlanModeRequest,
  listWorkflows as apiListWorkflows,
  createWorkflowApi,
  deleteWorkflowApi,
  setActiveWorkflow as apiSetActiveWorkflow,
  renameWorkflowApi,
} from '../services/evolvingApi';
import { useAgentSSE } from '../hooks/useAgentSSE';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  toolName?: string;
  toolInput?: Record<string, unknown>;
  timestamp: number;
}

export interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
}

interface AgentContextType {
  agents: AgentInfo[];
  activeAgent: AgentInfo | null;
  ontology: OntologyData['ontology'] | null;
  discoveries: Discovery[];
  messages: ChatMessage[];
  sessionId: string;
  isStreaming: boolean;
  batchProgress: BatchProgress | null;
  notifications: ReturnType<typeof useAgentSSE>['notifications'];
  sseConnected: boolean;
  mode: 'plan' | 'agent';
  plan: Plan | null;
  todos: TodoItem[];
  uploadedFiles: Array<{ name: string; path: string }>;

  deletedAgents: DeletedAgentInfo[];

  workflows: WorkflowListItem[];
  activeWorkflowId: string | null;
  loadWorkflows: (agentId?: string) => Promise<void>;
  selectWorkflow: (workflowId: string) => Promise<void>;
  createNewWorkflow: (name: string) => Promise<void>;
  removeWorkflow: (workflowId: string) => Promise<void>;
  renameWorkflow: (workflowId: string, name: string) => Promise<void>;

  loadAgents: () => Promise<void>;
  selectAgent: (agentId: string) => Promise<void>;
  createAgent: (name: string, purpose: string, llmProvider?: string, llmModel?: string) => Promise<AgentInfo>;
  removeAgent: (agentId: string) => Promise<void>;
  restoreAgent: (agentId: string) => Promise<void>;
  purgeAgent: (agentId: string) => Promise<void>;
  refreshDeletedAgents: () => Promise<void>;
  sendMessage: (text: string) => void;
  editAndResend: (messageId: string) => string | null;
  rewindAndResend: (messageId: string) => Promise<{
    deleted_records?: number;
    deleted_files?: number;
    ontology_versions_deleted?: number;
  } | null>;
  uploadFiles: (files: File[]) => Promise<void>;
  cancelStream: () => void;
  refreshOntology: () => Promise<void>;
  refreshDiscoveries: (status?: string) => Promise<void>;
  approveDiscovery: (name: string, type: string) => Promise<void>;
  rejectDiscovery: (name: string, type: string) => Promise<void>;
  refreshBatch: (batchId?: string) => Promise<void>;
  clearMessages: () => void;
  resetChat: () => Promise<void>;
  switchMode: (mode: 'plan' | 'agent') => Promise<void>;
  refreshPlan: () => Promise<void>;
  updatePlanStep: (stepId: number, content: string) => Promise<void>;
  addPlanStep: (afterStepId: number, content: string) => Promise<void>;
  removePlanStep: (stepId: number) => Promise<void>;
  clearPlan: () => Promise<void>;
  respondPlanRequest: (decision: 'approve' | 'reject', reason?: string, topic?: string) => Promise<void>;
  clearUploadedFiles: () => void;
}

const AgentContext = createContext<AgentContextType | null>(null);

export function AgentProvider({ children }: { children: ReactNode }) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [activeAgent, setActiveAgent] = useState<AgentInfo | null>(null);
  const [ontology, setOntology] = useState<OntologyData['ontology'] | null>(null);
  const [discoveries, setDiscoveries] = useState<Discovery[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [batchProgress, setBatchProgress] = useState<BatchProgress | null>(null);
  const [mode, setMode] = useState<'plan' | 'agent'>('agent');
  const [plan, setPlan] = useState<Plan | null>(null);
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<Array<{ name: string; path: string }>>([]);
  const [deletedAgents, setDeletedAgents] = useState<DeletedAgentInfo[]>([]);

  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [activeWorkflowId, setActiveWorkflowId] = useState<string | null>(null);

  const loadWorkflows = useCallback(
    async (agentId?: string) => {
      const id = agentId || activeAgent?.agent_id;
      if (!id) return;
      try {
        const resp = await apiListWorkflows(id);
        setWorkflows(resp.data.workflows || []);
        setActiveWorkflowId(resp.data.active_workflow_id || (resp.data.workflows?.[0]?.workflow_id ?? null));
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const abortRef = useRef<AbortController | null>(null);
  const { notifications, connected: sseConnected } = useAgentSSE(activeAgent?.agent_id ?? null);

  // Async batch durability: track which SSE notifications we've already
  // converted into chat messages so a tab switch / reconnection doesn't
  // duplicate them.
  const handledInjectionsRef = useRef<Set<string>>(new Set());
  // Buffer streaming chunks for system-event responses keyed by session_id.
  const injectionBuffersRef = useRef<Map<string, { id: string; content: string }>>(new Map());

  useEffect(() => {
    if (!activeAgent || notifications.length === 0) return;
    const latest = notifications[0];
    if (!latest) return;

    const data = (latest.data || {}) as Record<string, unknown>;
    const eventSession = (data.session_id as string) || '';
    const source = (data.source as string) || '';
    const isSystemEvent = source === 'system_event';

    // Only render system-event chunks/messages for the currently open session
    // (otherwise the user may be browsing a different thread).
    if (eventSession && sessionId && eventSession !== sessionId) return;

    if (latest.event_type === 'chat_message_chunk' && isSystemEvent) {
      const chunkContent = (data.content as string) || '';
      if (!chunkContent) return;
      const dedupeKey = `chunk-${latest.timestamp}-${eventSession}`;
      if (handledInjectionsRef.current.has(dedupeKey)) return;
      handledInjectionsRef.current.add(dedupeKey);

      const buffer = injectionBuffersRef.current.get(eventSession);
      if (buffer) {
        buffer.content += chunkContent;
        const targetId = buffer.id;
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.id === targetId);
          if (idx === -1) return prev;
          const updated = [...prev];
          updated[idx] = { ...updated[idx], content: updated[idx].content + chunkContent };
          return updated;
        });
      } else {
        const newId = `sys-${latest.timestamp}-${Math.random().toString(36).slice(2, 6)}`;
        injectionBuffersRef.current.set(eventSession, { id: newId, content: chunkContent });
        setMessages((prev) => [
          ...prev,
          {
            id: newId,
            role: 'assistant' as const,
            content: chunkContent,
            timestamp: Date.now(),
          },
        ]);
      }
      return;
    }

    if (latest.event_type === 'chat_message_injected') {
      const finalContent = (data.content as string) || '';
      const dedupeKey = `final-${latest.timestamp}-${eventSession}`;
      if (handledInjectionsRef.current.has(dedupeKey)) return;
      handledInjectionsRef.current.add(dedupeKey);

      const buffer = injectionBuffersRef.current.get(eventSession);
      if (buffer) {
        // Replace buffered content with the authoritative final text in case
        // chunk delivery dropped events.
        if (finalContent && finalContent !== buffer.content) {
          const targetId = buffer.id;
          setMessages((prev) => {
            const idx = prev.findIndex((m) => m.id === targetId);
            if (idx === -1) return prev;
            const updated = [...prev];
            updated[idx] = { ...updated[idx], content: finalContent };
            return updated;
          });
        }
        injectionBuffersRef.current.delete(eventSession);
      } else if (finalContent) {
        setMessages((prev) => [
          ...prev,
          {
            id: `sys-${latest.timestamp}-${Math.random().toString(36).slice(2, 6)}`,
            role: 'assistant' as const,
            content: finalContent,
            timestamp: Date.now(),
          },
        ]);
      }
    }
  }, [notifications, activeAgent, sessionId]);

  useEffect(() => {
    if (!activeAgent || notifications.length === 0) return;
    const latest = notifications[0];
    if (!latest) return;
    const et = latest.event_type;
    if (et === 'workflow_created' || et === 'workflow_deleted' || et === 'active_workflow_changed') {
      loadWorkflows(activeAgent.agent_id);
    }
  }, [notifications, activeAgent, loadWorkflows]);

  useEffect(() => {
    handledInjectionsRef.current.clear();
    injectionBuffersRef.current.clear();
  }, [activeAgent]);

  const loadAgents = useCallback(async () => {
    try {
      const resp = await listAgents();
      setAgents(resp.data);
    } catch {
      /* ignore */
    }
  }, []);

  const refreshOntology = useCallback(async () => {
    if (!activeAgent) return;
    try {
      const resp = await getOntology(activeAgent.agent_id);
      setOntology(resp.data.ontology);
    } catch {
      /* ignore */
    }
  }, [activeAgent]);

  const refreshDiscoveries = useCallback(
    async (status = '') => {
      if (!activeAgent) return;
      try {
        const resp = await listDiscoveries(activeAgent.agent_id, status);
        setDiscoveries(resp.data.discoveries);
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const selectAgent = useCallback(
    async (agentId: string) => {
      let found = agents.find((a) => a.agent_id === agentId);
      if (!found) {
        try {
          const resp = await apiGetAgent(agentId);
          found = resp.data;
          setAgents((prev) => {
            if (prev.some((a) => a.agent_id === agentId)) return prev;
            return [found!, ...prev];
          });
        } catch {
          return;
        }
      }
      setActiveAgent(found);
      setMessages([]);
      setSessionId('');
      setOntology(null);
      setDiscoveries([]);
      setBatchProgress(null);
      setPlan(null);
      setTodos([]);

      try {
        const resp = await getOntology(agentId);
        setOntology(resp.data.ontology);
      } catch {
        /* ignore */
      }
      try {
        const resp = await listDiscoveries(agentId, 'pending');
        setDiscoveries(resp.data.discoveries);
      } catch {
        /* ignore */
      }

      try {
        const sessResp = await listSessions(agentId);
        const sessions = sessResp.data.sessions ?? sessResp.data;
        if (Array.isArray(sessions) && sessions.length > 0) {
          const lastSessionId = sessions[sessions.length - 1].session_id;
          setSessionId(lastSessionId);
          try {
            const histResp = await getChatHistory(agentId, lastSessionId);
            const histMessages = histResp.data.messages || [];
            const loaded: ChatMessage[] = histMessages.map(
              (m: { role: string; content: string; tool_name?: string }, i: number) => ({
                id: `hist-${i}-${Date.now()}`,
                role: m.role === 'tool' || m.role === 'tool_call' ? 'tool' : m.role === 'user' ? 'user' : 'assistant',
                content: m.content || '',
                toolName: m.tool_name,
                timestamp: Date.now() - (histMessages.length - i) * 1000,
              })
            );
            if (loaded.length > 0) setMessages(loaded);
          } catch {
            /* history load failed, keep empty */
          }
        }
      } catch {
        /* ignore */
      }

      try {
        const modeResp = await apiGetMode(agentId);
        setMode(modeResp.data.mode);
        setPlan(modeResp.data.plan);
      } catch {
        setMode('plan');
      }

      try {
        const wfResp = await apiListWorkflows(agentId);
        setWorkflows(wfResp.data.workflows || []);
        setActiveWorkflowId(wfResp.data.active_workflow_id || (wfResp.data.workflows?.[0]?.workflow_id ?? null));
      } catch {
        setWorkflows([]);
        setActiveWorkflowId(null);
      }
    },
    [agents]
  );

  const createAgent = useCallback(
    async (name: string, purpose: string, llmProvider?: string, llmModel?: string) => {
      const resp = await apiCreateAgent(name, purpose, llmProvider, llmModel);
      const agent = resp.data;
      setAgents((prev) => [agent, ...prev]);
      setActiveAgent(agent);
      setMessages([]);
      setSessionId('');
      setOntology(null);
      setDiscoveries([]);
      setWorkflows([]);
      setActiveWorkflowId(null);
      return agent;
    },
    []
  );

  const selectWorkflow = useCallback(
    async (workflowId: string) => {
      const agentId = activeAgent?.agent_id;
      if (!agentId) return;
      try {
        await apiSetActiveWorkflow(agentId, workflowId);
        setActiveWorkflowId(workflowId);
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const createNewWorkflow = useCallback(
    async (name: string) => {
      const agentId = activeAgent?.agent_id;
      if (!agentId) return;
      try {
        const resp = await createWorkflowApi(agentId, name);
        setActiveWorkflowId(resp.data.workflow_id);
        await loadWorkflows(agentId);
      } catch {
        /* ignore */
      }
    },
    [activeAgent, loadWorkflows]
  );

  const removeWorkflow = useCallback(
    async (workflowId: string) => {
      const agentId = activeAgent?.agent_id;
      if (!agentId) return;
      try {
        await deleteWorkflowApi(agentId, workflowId);
        await loadWorkflows(agentId);
      } catch {
        /* ignore */
      }
    },
    [activeAgent, loadWorkflows]
  );

  const renameWorkflow = useCallback(
    async (workflowId: string, name: string) => {
      const agentId = activeAgent?.agent_id;
      if (!agentId) return;
      try {
        await renameWorkflowApi(agentId, workflowId, name);
        setWorkflows((prev) => prev.map((w) => (w.workflow_id === workflowId ? { ...w, name } : w)));
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const refreshDeletedAgents = useCallback(async () => {
    try {
      const resp = await apiListDeletedAgents();
      setDeletedAgents(resp.data.deleted || []);
    } catch {
      /* ignore */
    }
  }, []);

  const removeAgent = useCallback(
    async (agentId: string) => {
      // Soft delete (recoverable). Refreshes trash list so UI can show the new entry.
      await apiDeleteAgent(agentId, false);
      setAgents((prev) => prev.filter((a) => a.agent_id !== agentId));
      if (activeAgent?.agent_id === agentId) {
        setActiveAgent(null);
        setMessages([]);
        setOntology(null);
        setDiscoveries([]);
      }
      await refreshDeletedAgents();
    },
    [activeAgent, refreshDeletedAgents]
  );

  const restoreAgent = useCallback(
    async (agentId: string) => {
      await apiRestoreAgent(agentId);
      setDeletedAgents((prev) => prev.filter((a) => a.agent_id !== agentId));
      // Refresh active list so the restored agent reappears in the sidebar.
      try {
        const resp = await listAgents();
        setAgents(resp.data);
      } catch {
        /* ignore */
      }
    },
    []
  );

  const purgeAgent = useCallback(
    async (agentId: string) => {
      await apiPurgeAgent(agentId);
      setDeletedAgents((prev) => prev.filter((a) => a.agent_id !== agentId));
    },
    []
  );

  const sendMessage = useCallback(
    (text: string) => {
      if (!activeAgent || isStreaming) return;

      const now = Date.now();
      const userMsg: ChatMessage = {
        id: `u-${now}`,
        role: 'user',
        content: text,
        timestamp: now,
      };
      const assistantId = `a-${now}`;

      setMessages((prev) => [...prev, userMsg]);
      setIsStreaming(true);

      const controller = streamChat(
        activeAgent.agent_id,
        text,
        sessionId,
        (chunk: ChatChunk) => {
          if ((chunk.type === 'message_chunk' || chunk.type === 'token') && chunk.content) {
            setMessages((prev) => {
              const idx = prev.findIndex((m) => m.id === assistantId);
              if (idx >= 0) {
                const updated = [...prev];
                updated[idx] = { ...updated[idx], content: updated[idx].content + chunk.content };
                return updated;
              }
              return [
                ...prev,
                { id: assistantId, role: 'assistant' as const, content: chunk.content as string, timestamp: Date.now() },
              ];
            });
          } else if (chunk.type === 'tool_call') {
            setMessages((prev) => [
              ...prev,
              {
                id: `tc-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                role: 'tool' as const,
                content: '',
                toolName: chunk.tool_name || (chunk as any).name,
                toolInput: chunk.tool_input || (chunk as any).args,
                timestamp: Date.now(),
              },
            ]);
          } else if (chunk.type === 'tool_result') {
            const toolName = chunk.tool_name || (chunk as any).name;
            const resultText = (chunk as any).result || chunk.tool_output || '';
            setMessages((prev) => {
              // Ayni tool icin en son acik tool_call balonunu bul ve content'ini doldur.
              for (let i = prev.length - 1; i >= 0; i--) {
                const m = prev[i];
                if (m.role === 'tool' && m.toolName === toolName && !m.content) {
                  const updated = [...prev];
                  updated[i] = { ...m, content: resultText };
                  return updated;
                }
              }
              // Eslesme yoksa yeni bir tool balonu ekle.
              return [
                ...prev,
                {
                  id: `tr-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                  role: 'tool' as const,
                  content: resultText,
                  toolName,
                  timestamp: Date.now(),
                },
              ];
            });
          } else if (chunk.type === 'mode' && chunk.mode) {
            setMode(chunk.mode as 'plan' | 'agent');
          } else if (chunk.type === 'plan_update' && chunk.plan) {
            setPlan(chunk.plan as Plan);
          } else if (chunk.type === 'todo_update' && chunk.todos) {
            setTodos(chunk.todos as TodoItem[]);
          } else if (chunk.type === 'error' && chunk.content) {
            setMessages((prev) => [
              ...prev,
              {
                id: `err-${Date.now()}`,
                role: 'assistant' as const,
                content: `Bir hata olustu: ${chunk.content}`,
                timestamp: Date.now(),
              },
            ]);
          } else if (chunk.type === 'final_response') {
            if (chunk.session_id) setSessionId(chunk.session_id as string);
          }
        },
        () => {
          setIsStreaming(false);
          refreshOntology();
          refreshDiscoveries('pending');
        },
        () => setIsStreaming(false)
      );

      abortRef.current = controller;
    },
    [activeAgent, isStreaming, sessionId, refreshOntology, refreshDiscoveries]
  );

  const cancelStream = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const approveDiscoveryFn = useCallback(
    async (name: string, type: string) => {
      if (!activeAgent) return;
      await apiApprove(activeAgent.agent_id, name, type);
      await refreshDiscoveries('pending');
      await refreshOntology();
    },
    [activeAgent, refreshDiscoveries, refreshOntology]
  );

  const rejectDiscoveryFn = useCallback(
    async (name: string, type: string) => {
      if (!activeAgent) return;
      await apiReject(activeAgent.agent_id, name, type);
      await refreshDiscoveries('pending');
    },
    [activeAgent, refreshDiscoveries]
  );

  const refreshBatch = useCallback(
    async (batchId = '') => {
      if (!activeAgent) return;
      try {
        const resp = await apiBatchProgress(activeAgent.agent_id, batchId);
        setBatchProgress(resp.data);
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const uploadFilesFn = useCallback(
    async (files: File[]) => {
      if (!activeAgent || files.length === 0) return;
      try {
        const resp = await apiUploadFiles(activeAgent.agent_id, files);
        const names = files.map((f) => f.name).join(', ');
        const paths = resp.data.paths || [];

        const newUploaded = paths.map((p: string, i: number) => ({
          name: files[i]?.name || `file-${i}`,
          path: p,
        }));
        setUploadedFiles((prev) => [...prev, ...newUploaded]);

        setMessages((prev) => [
          ...prev,
          {
            id: `upload-${Date.now()}`,
            role: 'tool' as const,
            content: `${resp.data.file_count} dosya yuklendi: ${names}`,
            toolName: 'upload',
            timestamp: Date.now(),
          },
        ]);
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            role: 'assistant' as const,
            content: `Dosya yukleme hatasi: ${err}`,
            timestamp: Date.now(),
          },
        ]);
      }
    },
    [activeAgent]
  );

  const editAndResend = useCallback(
    (messageId: string): string | null => {
      const idx = messages.findIndex((m) => m.id === messageId);
      if (idx === -1) return null;
      const msg = messages[idx];
      if (msg.role !== 'user') return null;
      setMessages(messages.slice(0, idx));
      setSessionId('');
      return msg.content;
    },
    [messages]
  );

  const rewindAndResend = useCallback(
    async (messageId: string) => {
      if (!activeAgent) return null;
      const idx = messages.findIndex((m) => m.id === messageId);
      if (idx === -1) return null;
      const target = messages[idx];
      if (target.role !== 'user') return null;

      const userMessageIndex = messages
        .slice(0, idx)
        .filter((m) => m.role === 'user').length;

      let summary: {
        deleted_records?: number;
        deleted_files?: number;
        ontology_versions_deleted?: number;
      } | null = null;

      if (sessionId) {
        try {
          const resp = await apiRewindChat(activeAgent.agent_id, sessionId, userMessageIndex);
          summary = {
            deleted_records: resp.data.deleted_records,
            deleted_files: resp.data.deleted_files,
            ontology_versions_deleted: resp.data.ontology_versions_deleted,
          };
        } catch (err) {
          console.warn('Rewind backend hatasi, sadece UI temizlenecek:', err);
        }
      }

      setMessages(messages.slice(0, idx));
      await refreshOntology();
      sendMessage(target.content);
      return summary;
    },
    [activeAgent, messages, sessionId, refreshOntology, sendMessage]
  );

  const resetChatFn = useCallback(async () => {
    if (!activeAgent) return;
    try {
      await apiResetChat(activeAgent.agent_id);
    } catch { /* ignore */ }
    setMessages([]);
    setSessionId('');
  }, [activeAgent]);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setSessionId('');
  }, []);

  const switchModeFn = useCallback(
    async (newMode: 'plan' | 'agent') => {
      if (!activeAgent) return;
      try {
        await apiSwitchMode(activeAgent.agent_id, newMode);
        setMode(newMode);
        const planResp = await apiGetPlan(activeAgent.agent_id);
        setPlan(planResp.data.plan);
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const refreshPlanFn = useCallback(async () => {
    if (!activeAgent) return;
    try {
      const resp = await apiGetPlan(activeAgent.agent_id);
      setPlan(resp.data.plan);
    } catch {
      /* ignore */
    }
  }, [activeAgent]);

  const updatePlanStepFn = useCallback(
    async (stepId: number, content: string) => {
      if (!activeAgent) return;
      try {
        const resp = await apiUpdatePlanStep(activeAgent.agent_id, stepId, content);
        setPlan(resp.data.plan);
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const addPlanStepFn = useCallback(
    async (afterStepId: number, content: string) => {
      if (!activeAgent) return;
      try {
        const resp = await apiAddPlanStep(activeAgent.agent_id, afterStepId, content);
        setPlan(resp.data.plan);
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const removePlanStepFn = useCallback(
    async (stepId: number) => {
      if (!activeAgent) return;
      try {
        const resp = await apiRemovePlanStep(activeAgent.agent_id, stepId);
        setPlan(resp.data.plan);
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const clearPlanFn = useCallback(async () => {
    if (!activeAgent) return;
    try {
      await apiClearPlan(activeAgent.agent_id);
      setPlan(null);
      setMode('agent');
      setTodos([]);
    } catch {
      /* ignore */
    }
  }, [activeAgent]);

  const respondPlanRequestFn = useCallback(
    async (decision: 'approve' | 'reject', reason = '', topic = '') => {
      if (!activeAgent) return;
      try {
        const resp = await apiRespondPlanModeRequest(activeAgent.agent_id, decision, reason, topic);
        if (resp.data.decision === 'approved') {
          setMode('plan');
          const planResp = await apiGetPlan(activeAgent.agent_id);
          setPlan(planResp.data.plan);
        }
      } catch {
        /* ignore */
      }
    },
    [activeAgent]
  );

  const clearUploadedFilesFn = useCallback(() => setUploadedFiles([]), []);

  // Context value referansini memoize et: state degismedikce ayni nesne donsun ki
  // consumer'lar (PlanPanel, OntologyPanel, ResourcesPanel, BatchMonitor, AssistantChat)
  // gereksiz cascade re-render'a girmesin. Tab degisiminde Chakra Tabs'in icindeki
  // adapter setAdapter'lari icin bu kritik: yeni store referansi her render'da
  // yeni callback nesnelerine sebep oluyordu.
  const value = useMemo<AgentContextType>(() => ({
    agents,
    activeAgent,
    ontology,
    discoveries,
    messages,
    sessionId,
    isStreaming,
    batchProgress,
    notifications,
    sseConnected,
    mode,
    plan,
    todos,
    deletedAgents,
    workflows,
    activeWorkflowId,
    loadWorkflows,
    selectWorkflow,
    createNewWorkflow,
    removeWorkflow,
    renameWorkflow,
    loadAgents,
    selectAgent,
    createAgent,
    removeAgent,
    restoreAgent,
    purgeAgent,
    refreshDeletedAgents,
    sendMessage,
    editAndResend,
    rewindAndResend,
    uploadFiles: uploadFilesFn,
    cancelStream,
    refreshOntology,
    refreshDiscoveries,
    approveDiscovery: approveDiscoveryFn,
    rejectDiscovery: rejectDiscoveryFn,
    refreshBatch,
    clearMessages,
    resetChat: resetChatFn,
    switchMode: switchModeFn,
    refreshPlan: refreshPlanFn,
    updatePlanStep: updatePlanStepFn,
    addPlanStep: addPlanStepFn,
    removePlanStep: removePlanStepFn,
    clearPlan: clearPlanFn,
    respondPlanRequest: respondPlanRequestFn,
    uploadedFiles,
    clearUploadedFiles: clearUploadedFilesFn,
  }), [
    agents, activeAgent, ontology, discoveries, messages, sessionId, isStreaming,
    batchProgress, notifications, sseConnected, mode, plan, todos, deletedAgents,
    workflows, activeWorkflowId, uploadedFiles,
    loadWorkflows, selectWorkflow, createNewWorkflow, removeWorkflow, renameWorkflow,
    loadAgents, selectAgent, createAgent, removeAgent, restoreAgent, purgeAgent,
    refreshDeletedAgents, sendMessage, editAndResend, rewindAndResend, uploadFilesFn,
    cancelStream, refreshOntology, refreshDiscoveries, approveDiscoveryFn,
    rejectDiscoveryFn, refreshBatch, clearMessages, resetChatFn, switchModeFn,
    refreshPlanFn, updatePlanStepFn, addPlanStepFn, removePlanStepFn, clearPlanFn,
    respondPlanRequestFn, clearUploadedFilesFn,
  ]);

  return (
    <AgentContext.Provider value={value}>
      {children}
    </AgentContext.Provider>
  );
}

export function useAgentContext(): AgentContextType {
  const ctx = useContext(AgentContext);
  if (!ctx) throw new Error('useAgentContext must be used within AgentProvider');
  return ctx;
}
