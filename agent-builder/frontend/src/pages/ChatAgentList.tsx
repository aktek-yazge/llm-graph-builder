import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ChatAgentSummary,
  ChatAgentStatus,
  createChatAgent,
  listChatAgents,
} from '../services/chatAgentApi';
import Breadcrumb from '../components/Breadcrumb';

const TENANT_ID = 'default';

const statusColors: Record<ChatAgentStatus, string> = {
  draft: 'bg-gray-100 text-gray-700',
  deploying: 'bg-yellow-100 text-yellow-700',
  active: 'bg-green-100 text-green-700',
  inactive: 'bg-red-100 text-red-700',
  error: 'bg-red-200 text-red-800',
};

const statusLabels: Record<ChatAgentStatus, string> = {
  draft: 'Taslak',
  deploying: 'Dağıtılıyor',
  active: 'Aktif',
  inactive: 'Pasif',
  error: 'Hata',
};

export default function ChatAgentList() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<ChatAgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    try {
      const data = await listChatAgents(TENANT_ID);
      setAgents(data.agents);
    } catch (err) {
      console.error('Failed to load chat agents:', err);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    if (!name.trim()) return;
    setCreating(true);
    try {
      const result = await createChatAgent({
        name: name.trim(),
        description: desc.trim(),
        tenant_id: TENANT_ID,
      });
      navigate(`/chat-agents/${result.id}`);
    } catch (err) {
      console.error('Failed to create chat agent:', err);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <Breadcrumb items={[
        { label: 'Dashboard', to: '/dashboard' },
        { label: 'Chat Agents' },
      ]} />

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Chat Agents</h1>
          <p className="text-sm text-gray-500">
            MCP Gateway uzerinde Virtual Server olarak calisan Chat Agent'lari yonetin.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-medium"
        >
          + Yeni Agent
        </button>
      </div>

      {/* Create Form */}
      {showCreate && (
        <div className="bg-white rounded-xl border border-gray-200 p-6 mb-6">
          <h3 className="text-lg font-semibold mb-4">Yeni Chat Agent Oluştur</h3>
          <div className="space-y-3">
            <input
              type="text"
              placeholder="Agent adı"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
            />
            <textarea
              placeholder="Açıklama (isteğe bağlı)"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              rows={2}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none resize-none"
            />
            <div className="flex gap-2">
              <button
                onClick={handleCreate}
                disabled={creating || !name.trim()}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-medium"
              >
                {creating ? 'Oluşturuluyor...' : 'Oluştur'}
              </button>
              <button
                onClick={() => setShowCreate(false)}
                className="px-4 py-2 text-gray-600 hover:text-gray-800 text-sm"
              >
                İptal
              </button>
            </div>
          </div>
        </div>
      )}

      {/* List */}
      {loading ? (
        <div className="text-center py-12 text-gray-500">Yükleniyor...</div>
      ) : agents.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl border border-gray-200">
          <div className="text-4xl mb-3">🤖</div>
          <p className="text-gray-500 mb-4">Henüz Chat Agent oluşturulmamış.</p>
          <button
            onClick={() => setShowCreate(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm"
          >
            İlk Agent'ı Oluştur
          </button>
        </div>
      ) : (
        <div className="grid gap-4">
          {agents.map((agent) => (
            <Link
              key={agent.id}
              to={`/chat-agents/${agent.id}`}
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
                  </div>
                  {agent.description && (
                    <p className="text-sm text-gray-500 truncate">{agent.description}</p>
                  )}
                </div>
                <div className="flex items-center gap-4 text-xs text-gray-400 ml-4 shrink-0">
                  <span title="Tools">{agent.tool_count} tool</span>
                  <span title="Prompts">{agent.prompt_count} prompt</span>
                  <span title="Resources">{agent.resource_count} resource</span>
                </div>
              </div>
              {agent.gateway_server_id && (
                <div className="mt-2 text-xs text-gray-400">
                  Gateway: {agent.gateway_server_id.slice(0, 8)}...
                </div>
              )}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
