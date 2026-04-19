import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ChatAgentSummary,
  ChatAgentStatus,
  listChatAgents,
} from '../services/chatAgentApi';
import { listAgents as listEvolvingAgents, AgentInfo } from '../services/evolvingApi';
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

export default function ChatAgentList() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<ChatAgentSummary[]>([]);
  const [evolvingAgents, setEvolvingAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    try {
      const [agentData, evolvingData] = await Promise.allSettled([
        listChatAgents(TENANT_ID),
        listEvolvingAgents(50),
      ]);
      if (agentData.status === 'fulfilled') setAgents(agentData.value.agents);
      if (evolvingData.status === 'fulfilled') setEvolvingAgents(evolvingData.value.data);
    } catch (err) {
      console.error('Failed to load agents:', err);
    } finally {
      setLoading(false);
    }
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
            Agent ile konusarak Knowledge Graph olusturun ve yonetin.
          </p>
        </div>
        <button
          onClick={() => navigate('/agents')}
          className="inline-flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-amber-500 to-orange-600 text-white
                     rounded-xl hover:from-amber-600 hover:to-orange-700 transition-all text-sm font-medium
                     shadow-sm hover:shadow-md active:scale-[0.97]"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Yeni Agent Yarat
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-amber-200 border-t-amber-600" />
        </div>
      ) : evolvingAgents.length === 0 && agents.length === 0 ? (
        <div className="text-center py-20 bg-white rounded-2xl border border-gray-200">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-amber-100 to-orange-100 flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
          </div>
          <p className="text-gray-500 mb-1">Henuz Agent olusturulmamis.</p>
          <p className="text-sm text-gray-400 mb-6">
            Agent ile konusarak domain, entity ve relationship tanimlayabilirsiniz.
          </p>
          <button
            onClick={() => navigate('/agents')}
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-amber-500 to-orange-600 text-white
                       rounded-xl hover:from-amber-600 hover:to-orange-700 text-sm font-medium shadow-sm"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Ilk Agent'i Olustur
          </button>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Evolving Agents */}
          {evolvingAgents.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
              <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-slate-800">Self-Evolving Agent'lar</h2>
                  <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                    {evolvingAgents.length}
                  </span>
                </div>
              </div>
              <div className="divide-y divide-slate-50">
                {evolvingAgents.map((agent) => (
                  <div
                    key={agent.agent_id}
                    onClick={() => navigate('/agents')}
                    className="flex items-center justify-between px-5 py-4 hover:bg-amber-50/40 cursor-pointer transition-colors group"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center text-white text-sm font-bold shrink-0">
                        {agent.name?.[0]?.toUpperCase() || 'A'}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-slate-800 group-hover:text-amber-700 transition-colors truncate">
                            {agent.name || agent.agent_id.slice(0, 12)}
                          </span>
                          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium ${
                            agent.is_empty ? 'bg-slate-100 text-slate-600' : 'bg-emerald-50 text-emerald-700'
                          }`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${agent.is_empty ? 'bg-slate-400' : 'bg-emerald-500'}`} />
                            {agent.is_empty ? 'Bos' : 'Aktif'}
                          </span>
                        </div>
                        {agent.purpose && (
                          <p className="text-xs text-slate-400 truncate max-w-xs">{agent.purpose}</p>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-slate-400 shrink-0 ml-4">
                      {agent.domain && <span className="text-slate-500">{agent.domain}</span>}
                      <span className="tabular-nums">{agent.entity_count} entity</span>
                      <span className="tabular-nums">{agent.relationship_count} rel</span>
                      <svg className="w-4 h-4 text-slate-300 group-hover:text-amber-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Legacy Chat Agents */}
          {agents.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
              <div className="px-5 py-4 border-b border-slate-100">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-slate-800">Diger Agent'lar</h2>
                  <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                    {agents.length}
                  </span>
                </div>
              </div>
              <div className="divide-y divide-slate-50">
                {agents.map((agent) => (
                  <Link
                    key={agent.id}
                    to={`/admin/agents/${agent.id}`}
                    className="flex items-center justify-between px-5 py-4 hover:bg-blue-50/40 transition-colors group"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-violet-400 to-purple-500 flex items-center justify-center text-white text-sm font-bold shrink-0">
                        {agent.name?.[0]?.toUpperCase() || 'A'}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-slate-800 group-hover:text-blue-700 transition-colors truncate">
                            {agent.name}
                          </span>
                          <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${statusColors[agent.status]}`}>
                            {statusLabels[agent.status]}
                          </span>
                          <span className="px-2 py-0.5 rounded-md text-xs font-medium bg-purple-50 text-purple-700">
                            {agentTypeLabels[agent.agent_type] || agent.agent_type}
                          </span>
                        </div>
                        {agent.description && (
                          <p className="text-xs text-slate-400 truncate max-w-xs">{agent.description}</p>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-slate-400 shrink-0 ml-4">
                      <span>{agent.tool_count} tool</span>
                      <span>{agent.prompt_count} prompt</span>
                      {agent.workspace_ids.length > 0 && (
                        <span>{agent.workspace_ids.length} ws</span>
                      )}
                      <svg className="w-4 h-4 text-slate-300 group-hover:text-blue-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
