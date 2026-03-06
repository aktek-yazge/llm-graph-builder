import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ChatAgentSummary,
  ChatAgentStatus,
  createChatAgent,
  listChatAgents,
  sendCreatorChat,
} from '../services/chatAgentApi';
import { workspaceApi } from '../services/workspaceApi';
import Breadcrumb from '../components/Breadcrumb';

const TENANT_ID = 'default-tenant';

const statusColors: Record<ChatAgentStatus, string> = {
  draft: 'bg-gray-100 text-gray-700',
  deploying: 'bg-yellow-100 text-yellow-700',
  active: 'bg-green-100 text-green-700',
  inactive: 'bg-red-100 text-red-700',
  error: 'bg-red-200 text-red-800',
};

const statusLabels: Record<ChatAgentStatus, string> = {
  draft: 'Taslak',
  deploying: 'Dagitiliyor',
  active: 'Aktif',
  inactive: 'Pasif',
  error: 'Hata',
};

const agentTypeLabels: Record<string, string> = {
  expert: 'Uzman',
  analyst: 'Analist',
  assistant: 'Asistan',
};

interface WizardState {
  step: 1 | 2 | 3;
  name: string;
  description: string;
  agentType: string;
  selectedWorkspaces: string[];
  connectedAgents: string[];
  creatorSessionId: string;
  creatorMessages: Array<{ role: 'user' | 'assistant' | 'status'; content: string }>;
  createdAgentId?: string;
}

export default function ChatAgentList() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<ChatAgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showWizard, setShowWizard] = useState(false);
  const [workspaces, setWorkspaces] = useState<Array<{ id: string; name: string; status: string }>>([]);
  const [wizard, setWizard] = useState<WizardState>({
    step: 1,
    name: '',
    description: '',
    agentType: 'expert',
    selectedWorkspaces: [],
    connectedAgents: [],
    creatorSessionId: '',
    creatorMessages: [],
  });
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    try {
      const [agentData, wsData] = await Promise.all([
        listChatAgents(TENANT_ID),
        workspaceApi.listWorkspaces(TENANT_ID).then((ws) => ({ workspaces: ws })).catch(() => ({ workspaces: [] })),
      ]);
      setAgents(agentData.agents);
      setWorkspaces(
        (wsData as any).workspaces?.map((w: any) => ({
          id: w.id,
          name: w.name,
          status: w.status,
        })) || [],
      );
    } catch (err) {
      console.error('Failed to load agents:', err);
    } finally {
      setLoading(false);
    }
  }

  function resetWizard() {
    setWizard({
      step: 1,
      name: '',
      description: '',
      agentType: 'expert',
      selectedWorkspaces: [],
      connectedAgents: [],
      creatorSessionId: '',
      creatorMessages: [],
    });
    setChatInput('');
    setShowWizard(false);
  }

  async function goToStep2() {
    if (!wizard.name.trim() || wizard.selectedWorkspaces.length === 0) return;
    const sessionId = `creator-${Date.now()}`;
    setWizard((w) => ({
      ...w,
      step: 2,
      creatorSessionId: sessionId,
      creatorMessages: [{ role: 'status', content: 'Workspace analizi baslatiliyor...' }],
    }));

    setChatLoading(true);
    try {
      await sendCreatorChat(
        {
          message: `Agent adi: ${wizard.name}. Aciklama: ${wizard.description || 'Yok'}. Agent tipi: ${wizard.agentType}.`,
          session_id: sessionId,
          workspace_ids: wizard.selectedWorkspaces,
          agent_name: wizard.name,
          agent_type: wizard.agentType,
        },
        (data) => {
          try {
            const parsed = JSON.parse(data);
            setWizard((w) => ({
              ...w,
              creatorMessages: [
                ...w.creatorMessages,
                {
                  role: parsed.type === 'status' ? 'status' : 'assistant',
                  content: parsed.content || parsed.message || '',
                },
              ],
              createdAgentId: parsed.agent_id || w.createdAgentId,
            }));
          } catch {
            /* ignore parse errors */
          }
        },
        () => setChatLoading(false),
        (err) => {
          console.error(err);
          setChatLoading(false);
        },
      );
    } catch {
      setChatLoading(false);
    }
  }

  async function sendCreatorMessage() {
    if (!chatInput.trim() || chatLoading) return;
    const msg = chatInput.trim();
    setChatInput('');
    setWizard((w) => ({
      ...w,
      creatorMessages: [...w.creatorMessages, { role: 'user', content: msg }],
    }));
    setChatLoading(true);

    await sendCreatorChat(
      { message: msg, session_id: wizard.creatorSessionId },
      (data) => {
        try {
          const parsed = JSON.parse(data);
          setWizard((w) => ({
            ...w,
            creatorMessages: [
              ...w.creatorMessages,
              {
                role: parsed.type === 'status' ? 'status' : 'assistant',
                content: parsed.content || parsed.message || '',
              },
            ],
            createdAgentId: parsed.agent_id || w.createdAgentId,
          }));
          if (parsed.type === 'agent_created') {
            setWizard((w) => ({ ...w, step: 3 }));
          }
        } catch {
          /* ignore */
        }
      },
      () => setChatLoading(false),
      () => setChatLoading(false),
    );
  }

  function toggleWorkspace(wsId: string) {
    setWizard((w) => ({
      ...w,
      selectedWorkspaces: w.selectedWorkspaces.includes(wsId)
        ? w.selectedWorkspaces.filter((id) => id !== wsId)
        : [...w.selectedWorkspaces, wsId],
    }));
  }

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <Breadcrumb
        items={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Agent Yonetimi' }]}
      />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Agent Yonetimi</h1>
          <p className="text-sm text-gray-500">
            Uzman agentlar olusturun, workspace KB'lerine baglayın ve ContextForge'a deploy edin.
          </p>
        </div>
        <button
          onClick={() => setShowWizard(true)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-medium"
        >
          + Yeni Agent Yarat
        </button>
      </div>

      {/* Hybrid Wizard */}
      {showWizard && (
        <div className="bg-white rounded-xl border border-gray-200 p-6 mb-6">
          {/* Step Indicator */}
          <div className="flex items-center gap-4 mb-6">
            {[1, 2, 3].map((s) => (
              <div key={s} className="flex items-center gap-2">
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                    wizard.step === s
                      ? 'bg-blue-600 text-white'
                      : wizard.step > s
                        ? 'bg-green-500 text-white'
                        : 'bg-gray-200 text-gray-500'
                  }`}
                >
                  {wizard.step > s ? '\u2713' : s}
                </div>
                <span className="text-sm text-gray-600">
                  {s === 1 ? 'Temel Bilgiler' : s === 2 ? 'KB Analiz & Yapilandirma' : 'Tamamlandi'}
                </span>
                {s < 3 && <div className="w-8 h-px bg-gray-300" />}
              </div>
            ))}
          </div>

          {/* Step 1: Form */}
          {wizard.step === 1 && (
            <div className="space-y-4">
              <h3 className="text-lg font-semibold">Agent Bilgileri</h3>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Agent Adi *</label>
                  <input
                    type="text"
                    value={wizard.name}
                    onChange={(e) => setWizard((w) => ({ ...w, name: e.target.value }))}
                    placeholder="orn: Finans Analisti"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Agent Tipi</label>
                  <select
                    value={wizard.agentType}
                    onChange={(e) => setWizard((w) => ({ ...w, agentType: e.target.value }))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                  >
                    <option value="expert">Uzman (Expert)</option>
                    <option value="analyst">Analist (Analyst)</option>
                    <option value="assistant">Asistan (Assistant)</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Aciklama</label>
                <textarea
                  value={wizard.description}
                  onChange={(e) => setWizard((w) => ({ ...w, description: e.target.value }))}
                  rows={2}
                  placeholder="Agent'in gorev tanimi..."
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none resize-none"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Workspace Secimi * (KB kaynaklari)
                </label>
                {workspaces.length === 0 ? (
                  <p className="text-sm text-gray-400">Henuz workspace olusturulmamis.</p>
                ) : (
                  <div className="grid grid-cols-3 gap-2">
                    {workspaces.map((ws) => (
                      <button
                        key={ws.id}
                        onClick={() => toggleWorkspace(ws.id)}
                        className={`p-3 rounded-lg border text-left text-sm transition-all ${
                          wizard.selectedWorkspaces.includes(ws.id)
                            ? 'border-blue-500 bg-blue-50 text-blue-700'
                            : 'border-gray-200 hover:border-gray-300'
                        }`}
                      >
                        <div className="font-medium">{ws.name}</div>
                        <div className="text-xs text-gray-400">{ws.status}</div>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  onClick={goToStep2}
                  disabled={!wizard.name.trim() || wizard.selectedWorkspaces.length === 0}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-medium"
                >
                  Devam - KB Analiz
                </button>
                <button
                  onClick={resetWizard}
                  className="px-4 py-2 text-gray-600 hover:text-gray-800 text-sm"
                >
                  Iptal
                </button>
              </div>
            </div>
          )}

          {/* Step 2: Chat with Creator Assistant */}
          {wizard.step === 2 && (
            <div className="space-y-4">
              <h3 className="text-lg font-semibold">KB Analiz & Agent Yapilandirma</h3>
              <div className="bg-gray-50 rounded-lg p-4 max-h-96 overflow-y-auto space-y-3">
                {wizard.creatorMessages.map((msg, i) => (
                  <div
                    key={i}
                    className={`${
                      msg.role === 'user'
                        ? 'text-right'
                        : msg.role === 'status'
                          ? 'text-center'
                          : 'text-left'
                    }`}
                  >
                    {msg.role === 'status' ? (
                      <span className="text-xs text-gray-400 italic">{msg.content}</span>
                    ) : (
                      <div
                        className={`inline-block max-w-[80%] px-4 py-2 rounded-lg text-sm whitespace-pre-wrap ${
                          msg.role === 'user'
                            ? 'bg-blue-600 text-white'
                            : 'bg-white border border-gray-200 text-gray-800'
                        }`}
                      >
                        {msg.content}
                      </div>
                    )}
                  </div>
                ))}
                {chatLoading && (
                  <div className="text-center">
                    <span className="text-xs text-gray-400">Dusunuyor...</span>
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && sendCreatorMessage()}
                  placeholder="Mesajinizi yazin..."
                  disabled={chatLoading}
                  className="flex-1 px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none disabled:opacity-50"
                />
                <button
                  onClick={sendCreatorMessage}
                  disabled={chatLoading || !chatInput.trim()}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-medium"
                >
                  Gonder
                </button>
              </div>
            </div>
          )}

          {/* Step 3: Done */}
          {wizard.step === 3 && (
            <div className="text-center py-8">
              <div className="text-4xl mb-3">&#10003;</div>
              <h3 className="text-xl font-semibold text-green-700 mb-2">Agent Basariyla Olusturuldu!</h3>
              <p className="text-gray-500 mb-6">
                Agent'iniz deploy edildi ve kullanima hazir.
              </p>
              <div className="flex justify-center gap-3">
                <button
                  onClick={() => {
                    if (wizard.createdAgentId) {
                      navigate(`/admin/agents/${wizard.createdAgentId}`);
                    }
                    resetWizard();
                  }}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium"
                >
                  Agent Detayina Git
                </button>
                <button
                  onClick={() => {
                    resetWizard();
                    load();
                  }}
                  className="px-4 py-2 text-gray-600 hover:text-gray-800 text-sm"
                >
                  Kapat
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Agent List */}
      {loading ? (
        <div className="text-center py-12 text-gray-500">Yukleniyor...</div>
      ) : agents.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl border border-gray-200">
          <div className="text-4xl mb-3">&#129302;</div>
          <p className="text-gray-500 mb-4">Henuz Agent olusturulmamis.</p>
          <button
            onClick={() => setShowWizard(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm"
          >
            Ilk Agent'i Olustur
          </button>
        </div>
      ) : (
        <div className="grid gap-4">
          {agents.map((agent) => (
            <Link
              key={agent.id}
              to={`/admin/agents/${agent.id}`}
              className="block bg-white rounded-xl border border-gray-200 p-5 hover:border-blue-300 hover:shadow-sm transition-all"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-1">
                    <h3 className="font-semibold text-gray-900 truncate">{agent.name}</h3>
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColors[agent.status]}`}
                    >
                      {statusLabels[agent.status]}
                    </span>
                    <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
                      {agentTypeLabels[agent.agent_type] || agent.agent_type}
                    </span>
                    {agent.a2a_agent_id && (
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700">
                        A2A
                      </span>
                    )}
                  </div>
                  {agent.description && (
                    <p className="text-sm text-gray-500 truncate">{agent.description}</p>
                  )}
                  {agent.workspace_ids.length > 0 && (
                    <p className="text-xs text-gray-400 mt-1">
                      {agent.workspace_ids.length} workspace bagli
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-4 text-xs text-gray-400 ml-4 shrink-0">
                  <span title="Tools">{agent.tool_count} tool</span>
                  <span title="Prompts">{agent.prompt_count} prompt</span>
                  {agent.connected_agent_count > 0 && (
                    <span title="Connected Agents">{agent.connected_agent_count} bagli agent</span>
                  )}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
