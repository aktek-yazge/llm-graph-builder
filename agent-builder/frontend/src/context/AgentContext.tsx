import React, { createContext, useContext, useState, useCallback, useRef, type ReactNode } from 'react';
import {
  type AgentInfo,
  type OntologyData,
  type Discovery,
  type ChatChunk,
  type BatchProgress,
  listAgents,
  createAgent as apiCreateAgent,
  deleteAgent as apiDeleteAgent,
  getOntology,
  listDiscoveries,
  approveDiscovery as apiApprove,
  rejectDiscovery as apiReject,
  streamChat,
  getBatchProgress as apiBatchProgress,
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

  loadAgents: () => Promise<void>;
  selectAgent: (agentId: string) => Promise<void>;
  createAgent: (name: string, purpose: string) => Promise<AgentInfo>;
  removeAgent: (agentId: string) => Promise<void>;
  sendMessage: (text: string) => void;
  cancelStream: () => void;
  refreshOntology: () => Promise<void>;
  refreshDiscoveries: (status?: string) => Promise<void>;
  approveDiscovery: (name: string, type: string) => Promise<void>;
  rejectDiscovery: (name: string, type: string) => Promise<void>;
  refreshBatch: (batchId?: string) => Promise<void>;
  clearMessages: () => void;
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
      const found = agents.find((a) => a.agent_id === agentId);
      if (!found) return;
      setActiveAgent(found);
      setMessages([]);
      setSessionId('');
      setOntology(null);
      setDiscoveries([]);
      setBatchProgress(null);
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
          if (chunk.type === 'token' && chunk.content) {
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
                content: chunk.tool_output || '',
                toolName: chunk.tool_name,
                toolInput: chunk.tool_input,
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

  const clearMessages = useCallback(() => {
    setMessages([]);
    setSessionId('');
  }, []);

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
        loadAgents,
        selectAgent,
        createAgent,
        removeAgent,
        sendMessage,
        cancelStream,
        refreshOntology,
        refreshDiscoveries,
        approveDiscovery: approveDiscoveryFn,
        rejectDiscovery: rejectDiscoveryFn,
        refreshBatch,
        clearMessages,
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
