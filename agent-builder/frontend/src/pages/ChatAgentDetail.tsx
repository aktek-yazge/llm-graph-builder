import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ChatAgentDetail as AgentDetail,
  ChatAgentStatus,
  GatewayItem,
  getChatAgent,
  updateChatAgent,
  deleteChatAgent,
  deployChatAgent,
  undeployChatAgent,
  listGatewayTools,
  listGatewayPrompts,
  listGatewayResources,
  connectChat,
  sendChatMessage,
  disconnectChat,
} from '../services/chatAgentApi';
import Breadcrumb from '../components/Breadcrumb';

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

type Tab = 'overview' | 'tools' | 'prompts' | 'resources' | 'chat';

export default function ChatAgentDetailPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();

  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const [saving, setSaving] = useState(false);
  const [deploying, setDeploying] = useState(false);

  // Edit form
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editPrompt, setEditPrompt] = useState('');
  const [isEditing, setIsEditing] = useState(false);

  // Gateway items
  const [gwTools, setGwTools] = useState<GatewayItem[]>([]);
  const [gwPrompts, setGwPrompts] = useState<GatewayItem[]>([]);
  const [gwResources, setGwResources] = useState<GatewayItem[]>([]);
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [selectedPrompts, setSelectedPrompts] = useState<string[]>([]);
  const [selectedResources, setSelectedResources] = useState<string[]>([]);

  const loadAgent = useCallback(async () => {
    if (!agentId) return;
    try {
      const data = await getChatAgent(agentId);
      setAgent(data);
      setEditName(data.name);
      setEditDesc(data.description);
      setEditPrompt(data.system_prompt || '');
      setSelectedTools(data.associated_tools || []);
      setSelectedPrompts(data.associated_prompts || []);
      setSelectedResources(data.associated_resources || []);
    } catch (err) {
      console.error('Failed to load agent:', err);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  const loadGatewayItems = useCallback(async () => {
    try {
      const [t, p, r] = await Promise.all([
        listGatewayTools(),
        listGatewayPrompts(true),
        listGatewayResources(true),
      ]);
      setGwTools(t.tools);
      setGwPrompts(p.prompts);
      setGwResources(r.resources);
    } catch (err) {
      console.error('Failed to load gateway items:', err);
    }
  }, []);

  useEffect(() => {
    loadAgent();
    loadGatewayItems();
  }, [loadAgent, loadGatewayItems]);

  async function handleSave() {
    if (!agentId) return;
    setSaving(true);
    try {
      await updateChatAgent(agentId, {
        name: editName,
        description: editDesc,
        system_prompt: editPrompt || undefined,
        associated_tool_ids: selectedTools,
        associated_prompt_ids: selectedPrompts,
        associated_resource_ids: selectedResources,
      });
      setIsEditing(false);
      await loadAgent();
    } catch (err) {
      console.error('Save failed:', err);
    } finally {
      setSaving(false);
    }
  }

  async function handleDeploy() {
    if (!agentId) return;
    setDeploying(true);
    try {
      await deployChatAgent(agentId);
      await loadAgent();
    } catch (err) {
      console.error('Deploy failed:', err);
    } finally {
      setDeploying(false);
    }
  }

  async function handleUndeploy() {
    if (!agentId) return;
    setDeploying(true);
    try {
      await undeployChatAgent(agentId);
      await loadAgent();
    } catch (err) {
      console.error('Undeploy failed:', err);
    } finally {
      setDeploying(false);
    }
  }

  async function handleDelete() {
    if (!agentId || !confirm('Bu agent silinecek. Emin misiniz?')) return;
    try {
      await deleteChatAgent(agentId);
      navigate('/chat-agents');
    } catch (err) {
      console.error('Delete failed:', err);
    }
  }

  function toggleItem(list: string[], id: string, setter: (v: string[]) => void) {
    setter(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  if (loading) {
    return <div className="text-center py-16 text-gray-500">Yükleniyor...</div>;
  }
  if (!agent) {
    return <div className="text-center py-16 text-red-500">Agent bulunamadı</div>;
  }

  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: 'overview', label: 'Genel Bilgi' },
    { key: 'tools', label: 'Tools', count: selectedTools.length },
    { key: 'prompts', label: 'Prompts', count: selectedPrompts.length },
    { key: 'resources', label: 'Resources', count: selectedResources.length },
    { key: 'chat', label: 'Chat' },
  ];

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <Breadcrumb items={[
        { label: 'Dashboard', to: '/dashboard' },
        { label: 'Chat Agents', to: '/chat-agents' },
        { label: agent.name },
      ]} />

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">{agent.name}</h1>
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColors[agent.status]}`}>
              {statusLabels[agent.status]}
            </span>
          </div>
          {agent.description && (
            <p className="text-gray-500 mt-1">{agent.description}</p>
          )}
        </div>
        <div className="flex gap-2">
          {agent.status === 'active' ? (
            <button
              onClick={handleUndeploy}
              disabled={deploying}
              className="px-4 py-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 text-sm font-medium"
            >
              {deploying ? '...' : 'Devre Dışı Bırak'}
            </button>
          ) : (
            <button
              onClick={handleDeploy}
              disabled={deploying}
              className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 text-sm font-medium"
            >
              {deploying ? 'Dağıtılıyor...' : 'Gateway\'e Dağıt'}
            </button>
          )}
          <button
            onClick={() => setIsEditing(!isEditing)}
            className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 text-sm font-medium"
          >
            {isEditing ? 'İptal' : 'Düzenle'}
          </button>
          <button
            onClick={handleDelete}
            className="px-3 py-2 text-red-500 hover:text-red-700 hover:bg-red-50 rounded-lg text-sm"
            title="Sil"
          >
            🗑
          </button>
        </div>
      </div>

      {/* Connection Info */}
      {agent.gateway_server_id && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
          <div className="text-sm font-medium text-blue-800 mb-2">Gateway Bağlantı Bilgileri</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs text-blue-700">
            <div>
              <span className="font-medium">Server ID:</span>{' '}
              <code className="bg-blue-100 px-1 rounded">{agent.gateway_server_id}</code>
            </div>
            {agent.mcp_endpoint && (
              <div>
                <span className="font-medium">MCP:</span>{' '}
                <code className="bg-blue-100 px-1 rounded">{agent.mcp_endpoint}</code>
              </div>
            )}
            {agent.sse_endpoint && (
              <div>
                <span className="font-medium">SSE:</span>{' '}
                <code className="bg-blue-100 px-1 rounded">{agent.sse_endpoint}</code>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="flex gap-6">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.key
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {tab.label}
              {tab.count !== undefined && (
                <span className="ml-1.5 px-1.5 py-0.5 bg-gray-100 rounded-full text-xs">
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab Content */}
      {activeTab === 'overview' && (
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          {isEditing ? (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Ad</label>
                <input
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Açıklama</label>
                <textarea
                  value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)}
                  rows={2}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none resize-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">System Prompt</label>
                <textarea
                  value={editPrompt}
                  onChange={(e) => setEditPrompt(e.target.value)}
                  rows={6}
                  placeholder="Bu agent'ın sistem talimatlarını yazın..."
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none resize-none font-mono text-sm"
                />
              </div>
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-medium"
              >
                {saving ? 'Kaydediliyor...' : 'Kaydet'}
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <span className="text-gray-500">Tenant:</span>{' '}
                  <span className="text-gray-900">{agent.tenant_id}</span>
                </div>
                <div>
                  <span className="text-gray-500">Workspace:</span>{' '}
                  <span className="text-gray-900">{agent.workspace_id || '-'}</span>
                </div>
                <div>
                  <span className="text-gray-500">Oluşturulma:</span>{' '}
                  <span className="text-gray-900">
                    {agent.created_at ? new Date(agent.created_at).toLocaleString('tr-TR') : '-'}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500">Son Güncelleme:</span>{' '}
                  <span className="text-gray-900">
                    {agent.updated_at ? new Date(agent.updated_at).toLocaleString('tr-TR') : '-'}
                  </span>
                </div>
              </div>
              {agent.system_prompt && (
                <div className="mt-4 p-4 bg-gray-50 rounded-lg">
                  <h4 className="text-sm font-medium text-gray-700 mb-2">System Prompt</h4>
                  <pre className="text-sm text-gray-600 whitespace-pre-wrap font-mono">
                    {agent.system_prompt}
                  </pre>
                </div>
              )}
              {agent.tags.length > 0 && (
                <div className="flex gap-1.5 mt-2">
                  {agent.tags.map((tag) => (
                    <span
                      key={tag}
                      className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {activeTab === 'tools' && (
        <SelectorPanel
          title="Gateway Tools"
          items={gwTools}
          selected={selectedTools}
          originalSelected={agent.associated_tools || []}
          onToggle={(id) => toggleItem(selectedTools, id, setSelectedTools)}
          onSave={handleSave}
          saving={saving}
        />
      )}

      {activeTab === 'prompts' && (
        <SelectorPanel
          title="Gateway Prompts"
          items={gwPrompts}
          selected={selectedPrompts}
          originalSelected={agent.associated_prompts || []}
          onToggle={(id) => toggleItem(selectedPrompts, id, setSelectedPrompts)}
          onSave={handleSave}
          saving={saving}
        />
      )}

      {activeTab === 'resources' && (
        <SelectorPanel
          title="Gateway Resources"
          items={gwResources}
          selected={selectedResources}
          originalSelected={agent.associated_resources || []}
          onToggle={(id) => toggleItem(selectedResources, id, setSelectedResources)}
          onSave={handleSave}
          saving={saving}
        />
      )}

      {activeTab === 'chat' && (
        <ChatPanel
          agentId={agentId!}
          isDeployed={!!agent.gateway_server_id}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat panel — LLM Chat via Gateway proxy
// ---------------------------------------------------------------------------

const LLM_MODELS = [
  { value: 'gpt-4o', label: 'GPT-4o' },
  { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
  { value: 'gpt-4.1', label: 'GPT-4.1' },
  { value: 'claude-sonnet-4-20250514', label: 'Claude Sonnet 4' },
  { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
];

interface ChatEntry {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
}

function ChatPanel({ agentId, isDeployed }: { agentId: string; isDeployed: boolean }) {
  const [model, setModel] = useState('gpt-4o');
  const [temperature, setTemperature] = useState(0.7);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [userId, setUserId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  async function handleConnect() {
    setConnecting(true);
    try {
      const res = await connectChat(agentId, model, temperature);
      setUserId(res.user_id);
      setConnected(true);
      setMessages([{
        role: 'system',
        content: `Baglanti kuruldu. Model: ${model}`,
        timestamp: new Date(),
      }]);
    } catch (err) {
      console.error('Connect failed:', err);
      setMessages((prev) => [...prev, {
        role: 'system',
        content: `Baglanti hatasi: ${err}`,
        timestamp: new Date(),
      }]);
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnect() {
    abortRef.current?.abort();
    if (userId) {
      try {
        await disconnectChat(agentId, userId);
      } catch { /* ignore */ }
    }
    setConnected(false);
    setUserId(null);
    setSending(false);
    setMessages((prev) => [...prev, {
      role: 'system',
      content: 'Oturum sonlandirildi.',
      timestamp: new Date(),
    }]);
  }

  async function handleSend() {
    if (!input.trim() || !userId || sending) return;
    const userMsg = input.trim();
    setInput('');
    setSending(true);

    setMessages((prev) => [...prev, {
      role: 'user',
      content: userMsg,
      timestamp: new Date(),
    }]);

    const assistantIdx = messages.length + 1;
    setMessages((prev) => [...prev, {
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isStreaming: true,
    }]);

    try {
      const controller = await sendChatMessage(
        agentId,
        userId,
        userMsg,
        (chunk) => {
          try {
            const parsed = JSON.parse(chunk);
            const text = parsed.content || parsed.response || parsed.text || chunk;
            setMessages((prev) => {
              const updated = [...prev];
              const idx = assistantIdx;
              if (updated[idx]) {
                updated[idx] = { ...updated[idx], content: updated[idx].content + text };
              }
              return updated;
            });
          } catch {
            setMessages((prev) => {
              const updated = [...prev];
              const idx = assistantIdx;
              if (updated[idx]) {
                updated[idx] = { ...updated[idx], content: updated[idx].content + chunk };
              }
              return updated;
            });
          }
        },
        () => {
          setMessages((prev) => {
            const updated = [...prev];
            if (updated[assistantIdx]) {
              updated[assistantIdx] = { ...updated[assistantIdx], isStreaming: false };
            }
            return updated;
          });
          setSending(false);
        },
        (err) => {
          setMessages((prev) => {
            const updated = [...prev];
            if (updated[assistantIdx]) {
              updated[assistantIdx] = {
                ...updated[assistantIdx],
                content: updated[assistantIdx].content || `Hata: ${err.message}`,
                isStreaming: false,
              };
            }
            return updated;
          });
          setSending(false);
        },
      );
      abortRef.current = controller;
    } catch (err) {
      setSending(false);
    }
  }

  if (!isDeployed) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
        <div className="text-gray-400 text-4xl mb-4">💬</div>
        <h3 className="text-lg font-semibold text-gray-700 mb-2">Chat Kullanilabilir Degil</h3>
        <p className="text-sm text-gray-500">
          Chat ozelligini kullanmak icin once agent'i Gateway'e dagitmaniz gerekir.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 flex flex-col" style={{ height: '600px' }}>
      {/* Config bar */}
      <div className="flex items-center gap-4 px-4 py-3 border-b border-gray-200 bg-gray-50 rounded-t-xl">
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-gray-600">Model:</label>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={connected}
            className="text-sm border border-gray-300 rounded-lg px-2 py-1 bg-white disabled:bg-gray-100 disabled:text-gray-500"
          >
            {LLM_MODELS.map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-gray-600">Temp:</label>
          <input
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={temperature}
            onChange={(e) => setTemperature(parseFloat(e.target.value))}
            disabled={connected}
            className="w-16 text-sm border border-gray-300 rounded-lg px-2 py-1 disabled:bg-gray-100"
          />
        </div>

        <div className="flex-1" />

        {connected ? (
          <button
            onClick={handleDisconnect}
            className="px-4 py-1.5 bg-red-500 text-white rounded-lg hover:bg-red-600 text-sm font-medium"
          >
            Baglanitiyi Kes
          </button>
        ) : (
          <button
            onClick={handleConnect}
            disabled={connecting}
            className="px-4 py-1.5 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 text-sm font-medium"
          >
            {connecting ? 'Baglaniyor...' : 'Baglan'}
          </button>
        )}

        <div className={`w-2.5 h-2.5 rounded-full ${connected ? 'bg-green-500' : 'bg-gray-300'}`} />
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-gray-400 py-16 text-sm">
            Chat baslatmak icin baglanin ve bir mesaj gonderin.
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : msg.role === 'system'
                    ? 'bg-gray-100 text-gray-500 text-xs italic max-w-full text-center'
                    : 'bg-gray-100 text-gray-900'
              }`}
            >
              {msg.content}
              {msg.isStreaming && (
                <span className="inline-block w-2 h-4 bg-blue-500 animate-pulse ml-1 rounded-sm" />
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="border-t border-gray-200 px-4 py-3 bg-gray-50 rounded-b-xl">
        <form
          onSubmit={(e) => { e.preventDefault(); handleSend(); }}
          className="flex gap-2"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={connected ? 'Mesajinizi yazin...' : 'Once baglanin'}
            disabled={!connected || sending}
            className="flex-1 px-4 py-2.5 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 outline-none text-sm disabled:bg-gray-100 disabled:text-gray-400"
          />
          <button
            type="submit"
            disabled={!connected || sending || !input.trim()}
            className="px-5 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
          >
            {sending ? (
              <svg className="w-5 h-5 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              'Gonder'
            )}
          </button>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reusable selector panel for tools / prompts / resources
// ---------------------------------------------------------------------------

function SelectorPanel({
  title,
  items,
  selected: rawSelected,
  originalSelected: rawOriginal,
  onToggle,
  onSave,
  saving,
}: {
  title: string;
  items: GatewayItem[];
  selected: string[];
  originalSelected: string[];
  onToggle: (id: string) => void;
  onSave: () => void;
  saving: boolean;
}) {
  const selected = Array.isArray(rawSelected) ? rawSelected : [];
  const originalSelected = Array.isArray(rawOriginal) ? rawOriginal : [];

  const [filter, setFilter] = useState('');
  const filtered = filter
    ? items.filter(
        (i) =>
          i.name.toLowerCase().includes(filter.toLowerCase()) ||
          (i.description || '').toLowerCase().includes(filter.toLowerCase())
      )
    : items;

  const hasChanges =
    selected.length !== originalSelected.length ||
    selected.some((id) => !originalSelected.includes(id));

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-gray-900">{title}</h3>
        <span className="text-sm text-gray-400">{selected.length} secili / {items.length} toplam</span>
      </div>

      <input
        type="text"
        placeholder="Ara..."
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg mb-4 focus:ring-2 focus:ring-blue-500 outline-none text-sm"
      />

      {filtered.length === 0 ? (
        <p className="text-gray-400 text-sm py-4 text-center">
          {items.length === 0 ? 'Gateway\'de kayitli oge yok' : 'Sonuc bulunamadi'}
        </p>
      ) : (
        <div className="space-y-2 max-h-96 overflow-y-auto">
          {filtered.map((item) => {
            const isSelected = selected.includes(item.id);
            return (
              <div
                key={item.id}
                onClick={() => onToggle(item.id)}
                className={`flex items-center gap-3 p-3 rounded-lg border transition-colors cursor-pointer ${
                  isSelected
                    ? 'border-blue-300 bg-blue-50'
                    : 'border-gray-100 bg-gray-50 hover:border-blue-200'
                }`}
              >
                <div
                  className={`w-5 h-5 rounded border-2 flex items-center justify-center shrink-0 ${
                    isSelected
                      ? 'border-blue-500 bg-blue-500'
                      : 'border-gray-300'
                  }`}
                >
                  {isSelected && (
                    <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                      <path
                        fillRule="evenodd"
                        d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                        clipRule="evenodd"
                      />
                    </svg>
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-gray-900 truncate">{item.name}</div>
                  {item.description && (
                    <div className="text-xs text-gray-500 truncate">{item.description}</div>
                  )}
                </div>
                <code className="text-xs text-gray-300 shrink-0">{item.id.slice(0, 8)}</code>
              </div>
            );
          })}
        </div>
      )}

      {hasChanges && (
        <div className="mt-4 pt-4 border-t border-gray-100">
          <button
            onClick={onSave}
            disabled={saving}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-medium"
          >
            {saving ? 'Kaydediliyor...' : 'Degisiklikleri Kaydet'}
          </button>
        </div>
      )}
    </div>
  );
}
