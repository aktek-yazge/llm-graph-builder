import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  workspaceApi,
  workspaceChatApi,
  dashboardApi,
  Workspace,
  WorkspaceMonitoring,
  AgentEvent,
  ElicitationItem,
  BlackboardTopic,
} from '../services/workspaceApi';
import { resourceApi, Resource } from '../services/resourceApi';
import { RichMessagePart } from '../services/agentBuilderApi';
import ChatPanel, { ChatMessage } from '../components/ChatPanel';
import StatusPanel from '../components/StatusPanel';
import ElicitationPanel from '../components/ElicitationPanel';
import Breadcrumb from '../components/Breadcrumb';

type Phase = 'chat' | 'monitoring' | 'results';

const PHASE_LABELS: Record<string, { label: string; step: number }> = {
  created: { label: 'Baslangic', step: 1 },
  sampling: { label: 'Ornek Analiz', step: 2 },
  schema_review: { label: 'Schema Onay', step: 3 },
  agent_ready: { label: 'KB Agent', step: 4 },
  ready: { label: 'Hazir', step: 4 },
  processing: { label: 'Isleniyor', step: 5 },
  quality_check: { label: 'Inceleme', step: 5 },
  completed: { label: 'Tamamlandi', step: 6 },
  failed: { label: 'Hata', step: 6 },
};

function statusToPhase(status: string): Phase {
  if (['processing', 'quality_check'].includes(status)) return 'monitoring';
  if (['completed', 'failed'].includes(status)) return 'results';
  return 'chat';
}

export default function WorkspaceDetail() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [phase, setPhase] = useState<Phase>('chat');
  const [loading, setLoading] = useState(true);
  const [showSideUpload, setShowSideUpload] = useState(false);

  useEffect(() => {
    if (workspaceId) loadWorkspace();
  }, [workspaceId]);

  async function loadWorkspace() {
    try {
      const ws = await workspaceApi.getWorkspace(workspaceId!);
      setWorkspace(ws);
      setPhase(statusToPhase(ws.status));
    } catch (err) {
      console.error('Failed to load workspace:', err);
    } finally {
      setLoading(false);
    }
  }

  if (loading) return <div className="flex items-center justify-center h-screen text-gray-500">Yukleniyor...</div>;
  if (!workspace) return <div className="flex items-center justify-center h-screen text-red-500">Workspace bulunamadi</div>;

  const phaseInfo = PHASE_LABELS[workspace.status] || { label: workspace.status, step: 1 };

  return (
    <div className="h-screen flex flex-col">
      {/* Top Bar */}
      <div className="flex-shrink-0 bg-white border-b border-gray-200 px-6 py-3">
        <Breadcrumb items={[
          { label: 'Dashboard', to: '/dashboard' },
          { label: 'Workspaces', to: '/workspaces' },
          { label: workspace.name },
        ]} />
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-gray-900">{workspace.name}</h1>
            {workspace.description && <p className="text-xs text-gray-500">{workspace.description}</p>}
          </div>

          {/* Mini Status Indicator */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              {[1, 2, 3, 4, 5, 6].map((step) => (
                <div
                  key={step}
                  className={`w-2 h-2 rounded-full ${
                    step < phaseInfo.step ? 'bg-green-500'
                      : step === phaseInfo.step ? 'bg-blue-500 animate-pulse'
                        : 'bg-gray-200'
                  }`}
                  title={`Adim ${step}`}
                />
              ))}
            </div>
            <span className="text-xs text-gray-500 font-medium">{phaseInfo.label}</span>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Chat (always visible) */}
        <div className="flex-1 flex flex-col min-w-0 min-h-0">
          {phase === 'chat' && (
            <WorkspaceChatPane
              workspaceId={workspace.id}
              onTriggerSideUpload={() => setShowSideUpload(true)}
              onPhaseChange={(newStatus) => {
                if (statusToPhase(newStatus) !== 'chat') {
                  setPhase(statusToPhase(newStatus));
                }
                loadWorkspace();
              }}
            />
          )}
          {phase === 'monitoring' && <MonitoringPane workspaceId={workspace.id} />}
          {phase === 'results' && <ResultsPane workspaceId={workspace.id} />}
        </div>

        {/* Right: Status Panel */}
        <div className="w-72 flex-shrink-0 border-l border-gray-200 bg-gray-50 overflow-y-auto p-4">
          <StatusPanel
            workspaceId={workspace.id}
            showUpload={showSideUpload}
            onUploadComplete={() => setShowSideUpload(false)}
          />
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// WORKSPACE CHAT PANE (Main interaction - always visible)
// =============================================================================

function WorkspaceChatPane({
  workspaceId,
  onTriggerSideUpload,
  onPhaseChange,
}: {
  workspaceId: string;
  onTriggerSideUpload: () => void;
  onPhaseChange: (status: string) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState('');
  const [initializing, setInitializing] = useState(true);

  useEffect(() => {
    loadChatHistory();
  }, [workspaceId]);

  async function loadChatHistory() {
    try {
      const history = await workspaceChatApi.getHistory(workspaceId);

      if (history.session_id && history.messages.length > 0) {
        setSessionId(history.session_id);
        setMessages(
          history.messages.map((m) => ({
            role: m.role as 'user' | 'assistant',
            content: m.content,
          })),
        );
      } else {
        await initNewChat();
      }
    } catch (e) {
      console.error('Chat history load failed, starting new:', e);
      await initNewChat();
    } finally {
      setInitializing(false);
    }
  }

  async function initNewChat() {
    try {
      const res = await workspaceChatApi.chat(
        workspaceId,
        'Merhaba',
        undefined,
      );
      setSessionId(res.session_id);
      setMessages([
        buildAssistantMessage(res),
      ]);
      if (res.phase) onPhaseChange(res.phase);
    } catch (e) {
      console.error('Workspace Agent init failed:', e);
      setMessages([{
        role: 'assistant',
        content: 'Agent baglantisi kurulamadi. Lutfen sayfayi yenileyin.',
      }]);
    }
  }

  function buildAssistantMessage(res: {
    response: string;
    rich_parts?: RichMessagePart[];
  }): ChatMessage {
    return {
      role: 'assistant',
      content: res.response,
      richParts: res.rich_parts && res.rich_parts.length > 0 ? res.rich_parts as RichMessagePart[] : undefined,
    };
  }

  async function handleSend(message: string) {
    setMessages((prev) => [...prev, { role: 'user', content: message }]);
    setLoading(true);

    setMessages((prev) => [...prev, { role: 'assistant', content: '' }]);
    const streamIdx = messages.length + 1;

    try {
      await workspaceChatApi.chatStream(workspaceId, message, sessionId || undefined, {
        onChunk(text) {
          setMessages((prev) => {
            const updated = [...prev];
            if (updated[streamIdx]) {
              updated[streamIdx] = { ...updated[streamIdx], content: updated[streamIdx].content + text };
            }
            return updated;
          });
        },
        onToolCall(name) {
          setMessages((prev) => {
            const updated = [...prev];
            if (updated[streamIdx]) {
              updated[streamIdx] = { ...updated[streamIdx], content: updated[streamIdx].content + `\n[${name} calistiriliyor...]\n` };
            }
            return updated;
          });
        },
        onToolResult(name) {
          setMessages((prev) => {
            const updated = [...prev];
            if (updated[streamIdx]) {
              updated[streamIdx] = { ...updated[streamIdx], content: updated[streamIdx].content.replace(`[${name} calistiriliyor...]`, `[${name} tamamlandi]`) };
            }
            return updated;
          });
        },
        onDone(data) {
          if (!sessionId && data.session_id) setSessionId(data.session_id);
          setMessages((prev) => {
            const updated = [...prev];
            if (updated[streamIdx]) {
              updated[streamIdx] = {
                role: 'assistant',
                content: data.full_response,
                richParts: data.rich_parts && data.rich_parts.length > 0 ? data.rich_parts as RichMessagePart[] : undefined,
              };
            }
            return updated;
          });

          const hasSideUpload = data.rich_parts?.some(
            (p: { type: string }) => p.type === 'trigger_side_upload',
          );
          if (hasSideUpload) onTriggerSideUpload();
        },
        onError(error) {
          setMessages((prev) => {
            const updated = [...prev];
            if (updated[streamIdx]) {
              updated[streamIdx] = { role: 'assistant', content: `Hata: ${error}` };
            }
            return updated;
          });
        },
      });
    } catch (e) {
      setMessages((prev) => {
        const updated = [...prev];
        if (updated[streamIdx]) {
          updated[streamIdx] = { role: 'assistant', content: `Hata: ${(e as Error).message}` };
        }
        return updated;
      });
    } finally {
      setLoading(false);
    }
  }

  async function handleUploadFiles(files: File[]) {
    try {
      const list = await resourceApi.list();
      const attached = list.resources.find((r: Resource) => r.workspace_id === workspaceId);
      if (attached) {
        await resourceApi.upload(attached.id, files);
      } else {
        const res = await resourceApi.create({ name: `ws-${workspaceId}-docs` });
        await resourceApi.attachToWorkspace(res.id, workspaceId);
        await resourceApi.upload(res.id, files);
      }
      handleSend(`${files.length} dosya yuklendi.`);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Upload hatasi: ${(e as Error).message}` },
      ]);
    }
  }

  if (initializing) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-3" />
          <p className="text-sm text-gray-500">Agent ile baglanti kuruluyor...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <ChatPanel
        messages={messages}
        onSend={handleSend}
        onUploadFiles={handleUploadFiles}
        onTriggerSideUpload={onTriggerSideUpload}
        title=""
        placeholder="Mesajinizi yazin..."
        loading={loading}
      />
    </div>
  );
}

// =============================================================================
// MONITORING PANE
// =============================================================================

function MonitoringPane({ workspaceId }: { workspaceId: string }) {
  const [monitoring, setMonitoring] = useState<WorkspaceMonitoring | null>(null);
  const [liveEvents, setLiveEvents] = useState<AgentEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    loadMonitoring();
    const es = dashboardApi.subscribeToEvents(workspaceId, (event) => {
      setLiveEvents((prev) => [event, ...prev].slice(0, 50));
    });
    eventSourceRef.current = es;
    const interval = setInterval(loadMonitoring, 10000);
    return () => { es.close(); clearInterval(interval); };
  }, [workspaceId]);

  async function loadMonitoring() {
    try {
      const data = await workspaceApi.getMonitoring(workspaceId);
      setMonitoring(data);
    } catch (err) {
      console.error('Monitoring load failed:', err);
    } finally {
      setLoading(false);
    }
  }

  if (loading) return <div className="flex-1 flex items-center justify-center text-gray-500">Monitoring yukleniyor...</div>;
  if (!monitoring) return <div className="flex-1 flex items-center justify-center text-red-500">Monitoring verisi alinamadi</div>;

  const stats = monitoring.stats;
  const batch = monitoring.active_batch;

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MiniStat value={stats.total_documents} label="Toplam" color="gray" />
        <MiniStat value={stats.successful} label="Basarili" color="green" />
        <MiniStat value={stats.failed} label="Hatali" color="red" />
        <MiniStat value={stats.in_progress} label="Isleniyor" color="blue" />
      </div>

      {batch && (
        <div className="bg-white rounded-xl border border-blue-200 p-5">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
            <h3 className="text-sm font-semibold text-gray-700">Aktif Isleme</h3>
          </div>
          <div className="mb-2">
            <div className="flex justify-between text-sm mb-1">
              <span className="font-medium">{batch.processed} / {batch.total}</span>
              <span className="text-gray-500">%{batch.percent_complete.toFixed(1)}</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-3">
              <div className="bg-blue-600 h-3 rounded-full transition-all duration-500" style={{ width: `${Math.min(batch.percent_complete, 100)}%` }} />
            </div>
          </div>
        </div>
      )}

      <ElicitationPanel
        workspaceId={workspaceId}
        pendingCount={monitoring.elicitation.pending}
      />

      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">
          Aktivite
          {liveEvents.length > 0 && <span className="ml-2 w-2 h-2 bg-green-400 rounded-full inline-block animate-pulse" />}
        </h3>
        <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
          {[...liveEvents, ...monitoring.recent_events].slice(0, 20).map((ev, i) => (
            <div key={`${ev.event_id}-${i}`} className="flex items-center gap-2 text-xs py-1">
              <span className="text-gray-400 font-mono w-14 flex-shrink-0">
                {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }) : ''}
              </span>
              <span className={`px-1.5 py-0.5 rounded font-medium ${
                ev.event_type.includes('completed') ? 'bg-green-100 text-green-700' :
                ev.event_type.includes('failed') ? 'bg-red-100 text-red-700' :
                'bg-gray-100 text-gray-600'
              }`}>{ev.event_type.split('.').pop()}</span>
            </div>
          ))}
          {liveEvents.length === 0 && monitoring.recent_events.length === 0 && (
            <p className="text-sm text-gray-400 py-4 text-center">Henuz event yok</p>
          )}
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// RESULTS PANE
// =============================================================================

function ResultsPane({ workspaceId }: { workspaceId: string }) {
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      const data = await workspaceApi.getStats(workspaceId);
      setStats(data as unknown as Record<string, unknown>);
    } catch (err) {
      console.error('Failed to load results:', err);
    } finally {
      setLoading(false);
    }
  }

  if (loading) return <div className="flex-1 flex items-center justify-center text-gray-500">Sonuclar yukleniyor...</div>;

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { label: 'Toplam', value: stats.total_documents, color: 'gray' },
            { label: 'Basarili', value: stats.successful, color: 'green' },
            { label: 'Hatali', value: stats.failed, color: 'red' },
            { label: 'Basari Orani', value: `%${stats.success_rate}`, color: 'blue' },
          ].map((s) => (
            <MiniStat key={s.label} value={s.value as number} label={s.label} color={s.color} />
          ))}
        </div>
      )}

      <div className="bg-green-50 rounded-xl border border-green-200 p-8 text-center">
        <h3 className="text-lg font-semibold text-green-800">Isleme Tamamlandi</h3>
        <p className="text-green-600 mt-1">Knowledge Base hazir. KB Agent ile sorgulama yapabilirsiniz.</p>
      </div>
    </div>
  );
}

// =============================================================================
// HELPERS
// =============================================================================

function MiniStat({ value, label, color }: { value: number | string; label: string; color: string }) {
  const colors: Record<string, string> = {
    green: 'bg-green-50 text-green-700',
    red: 'bg-red-50 text-red-700',
    yellow: 'bg-yellow-50 text-yellow-700',
    blue: 'bg-blue-50 text-blue-700',
    gray: 'bg-gray-50 text-gray-700',
  };
  return (
    <div className={`${colors[color] || colors.gray} rounded-lg p-3`}>
      <div className="font-semibold text-lg">{String(value)}</div>
      <div className="text-xs">{label}</div>
    </div>
  );
}
