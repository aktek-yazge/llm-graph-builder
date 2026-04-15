import React, { createContext, useContext, useState, useCallback, useRef, type ReactNode } from 'react';
import {
  type AgentInfo,
  type OntologyData,
  type Discovery,
  type ChatChunk,
  type BatchProgress,
  type Plan,
  type PlanStep,
  listAgents,
  createAgent as apiCreateAgent,
  deleteAgent as apiDeleteAgent,
  getOntology,
  listDiscoveries,
  approveDiscovery as apiApprove,
  rejectDiscovery as apiReject,
  streamChat,
  getBatchProgress as apiBatchProgress,
  uploadFiles as apiUploadFiles,
  listSessions,
  getAgent as apiGetAgent,
  resetChat as apiResetChat,
  getMode as apiGetMode,
  switchMode as apiSwitchMode,
  getPlan as apiGetPlan,
  updatePlanStep as apiUpdatePlanStep,
  addPlanStep as apiAddPlanStep,
  removePlanStep as apiRemovePlanStep,
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

  loadAgents: () => Promise<void>;
  selectAgent: (agentId: string) => Promise<void>;
  createAgent: (name: string, purpose: string) => Promise<AgentInfo>;
  removeAgent: (agentId: string) => Promise<void>;
  sendMessage: (text: string) => void;
  editAndResend: (messageId: string) => string | null;
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
  const [mode, setMode] = useState<'plan' | 'agent'>('plan');
  const [plan, setPlan] = useState<Plan | null>(null);
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<Array<{ name: string; path: string }>>([]);

  const abortRef = useRef<AbortController | null>(null);
  const { notifications, connected: sseConnected } = useAgentSSE(activeAgent?.agent_id ?? null);

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
        if (sessResp.data.length > 0) {
          setSessionId(sessResp.data[sessResp.data.length - 1].session_id);
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
    },
    [agents]
  );

  const createAgent = useCallback(
    async (name: string, purpose: string) => {
      const resp = await apiCreateAgent(name, purpose);
      const agent = resp.data;
      setAgents((prev) => [agent, ...prev]);
      setActiveAgent(agent);
      setMessages([]);
      setSessionId('');
      setOntology(null);
      setDiscoveries([]);
      return agent;
    },
    []
  );

  const removeAgent = useCallback(
    async (agentId: string) => {
      await apiDeleteAgent(agentId);
      setAgents((prev) => prev.filter((a) => a.agent_id !== agentId));
      if (activeAgent?.agent_id === agentId) {
        setActiveAgent(null);
        setMessages([]);
        setOntology(null);
        setDiscoveries([]);
      }
    },
    [activeAgent]
  );

  const sendMessage = useCallback(
    (text: string) => {
      if (!activeAgent || isStreaming) return;

      const userMsg: ChatMessage = {
        id: `msg-${Date.now()}`,
        role: 'user',
        content: text,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setIsStreaming(true);

      let assistantContent = '';
      const assistantId = `msg-${Date.now() + 1}`;

      const controller = streamChat(
        activeAgent.agent_id,
        text,
        sessionId,
        (chunk: ChatChunk) => {
          if ((chunk.type === 'message_chunk' || chunk.type === 'token') && chunk.content) {
            assistantContent += chunk.content;
            setMessages((prev) => {
              const existing = prev.find((m) => m.id === assistantId);
              if (existing) {
                return prev.map((m) => (m.id === assistantId ? { ...m, content: assistantContent } : m));
              }
              return [
                ...prev,
                { id: assistantId, role: 'assistant' as const, content: assistantContent, timestamp: Date.now() },
              ];
            });
          } else if (chunk.type === 'tool_call') {
            setMessages((prev) => [
              ...prev,
              {
                id: `tool-${Date.now()}`,
                role: 'tool' as const,
                content: '',
                toolName: chunk.tool_name || (chunk as any).name,
                toolInput: chunk.tool_input || (chunk as any).args,
                timestamp: Date.now(),
              },
            ]);
          } else if (chunk.type === 'tool_result') {
            setMessages((prev) => [
              ...prev,
              {
                id: `toolres-${Date.now()}`,
                role: 'tool' as const,
                content: (chunk as any).result || chunk.tool_output || '',
                toolName: chunk.tool_name || (chunk as any).name,
                timestamp: Date.now(),
              },
            ]);
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
            if (chunk.content) {
              setMessages((prev) => {
                const existing = prev.find((m) => m.id === assistantId);
                if (existing) {
                  return prev.map((m) => (m.id === assistantId ? { ...m, content: chunk.content as string } : m));
                }
                return [
                  ...prev,
                  {
                    id: assistantId,
                    role: 'assistant' as const,
                    content: chunk.content as string,
                    timestamp: Date.now(),
                  },
                ];
              });
            }
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

  return (
    <AgentContext.Provider
      value={{
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
        loadAgents,
        selectAgent,
        createAgent,
        removeAgent,
        sendMessage,
        editAndResend,
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
        uploadedFiles,
        clearUploadedFiles: () => setUploadedFiles([]),
      }}
    >
      {children}
    </AgentContext.Provider>
  );
}

export function useAgentContext(): AgentContextType {
  const ctx = useContext(AgentContext);
  if (!ctx) throw new Error('useAgentContext must be used within AgentProvider');
  return ctx;
}
