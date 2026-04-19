import { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { AgentBuilderAPI, Agent as ApiAgent } from '../services/agentBuilderApi';
import {
  dashboardApi,
  DashboardOverview,
  AgentEvent,
} from '../services/workspaceApi';
import {
  listAgents as listEvolvingAgents,
  AgentInfo,
  listAllWorkflows,
  GlobalWorkflowItem,
  setActiveWorkflow,
  createAgent,
} from '../services/evolvingApi';
import Breadcrumb from '../components/Breadcrumb';

interface Agent extends ApiAgent {
  skill_count?: number;
  deployed?: boolean;
}

const STATUS_LABELS: Record<string, { label: string; color: string; dot: string }> = {
  created: { label: 'Olusturuldu', color: 'bg-slate-100 text-slate-700', dot: 'bg-slate-400' },
  sampling: { label: 'Analiz', color: 'bg-blue-50 text-blue-700', dot: 'bg-blue-500' },
  schema_proposed: { label: 'Schema Onerisi', color: 'bg-indigo-50 text-indigo-700', dot: 'bg-indigo-500' },
  schema_review: { label: 'Schema Inceleme', color: 'bg-purple-50 text-purple-700', dot: 'bg-purple-500' },
  schema_approved: { label: 'Schema Onayli', color: 'bg-cyan-50 text-cyan-700', dot: 'bg-cyan-500' },
  agent_ready: { label: 'Agent Hazir', color: 'bg-teal-50 text-teal-700', dot: 'bg-teal-500' },
  ready: { label: 'Hazir', color: 'bg-teal-50 text-teal-700', dot: 'bg-teal-500' },
  processing: { label: 'Isleniyor', color: 'bg-amber-50 text-amber-700', dot: 'bg-amber-500' },
  quality_check: { label: 'Inceleme', color: 'bg-orange-50 text-orange-700', dot: 'bg-orange-500' },
  completed: { label: 'Tamamlandi', color: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500' },
  failed: { label: 'Basarisiz', color: 'bg-red-50 text-red-700', dot: 'bg-red-500' },
};

const EVENT_LABELS: Record<string, { label: string; icon: string; color: string }> = {
  'processing.started': { label: 'Isleme basladi', icon: '▶', color: 'text-blue-500' },
  'processing.progress': { label: 'Ilerleme', icon: '⟳', color: 'text-blue-400' },
  'processing.completed': { label: 'Tamamlandi', icon: '✓', color: 'text-emerald-500' },
  'processing.failed': { label: 'Hata', icon: '✗', color: 'text-red-500' },
  'workspace.schema_approved': { label: 'Schema onaylandi', icon: '✓', color: 'text-emerald-500' },
  'workspace.schema_proposed': { label: 'Schema onerisi', icon: '◈', color: 'text-indigo-500' },
  'agent.kb_agent_created': { label: 'Agent olusturuldu', icon: '+', color: 'text-purple-500' },
  'elicitation.requested': { label: 'Inceleme gerekli', icon: '!', color: 'text-amber-500' },
  'elicitation.resolved': { label: 'Inceleme cozuldu', icon: '✓', color: 'text-emerald-500' },
  'agent.message': { label: 'Mesaj', icon: '✉', color: 'text-blue-500' },
  'blackboard.updated': { label: 'Blackboard', icon: '■', color: 'text-slate-500' },
};

const WF_STATUS_LABELS: Record<string, { label: string; color: string; dot: string }> = {
  draft: { label: 'Taslak', color: 'bg-slate-100 text-slate-700', dot: 'bg-slate-400' },
  published: { label: 'Yayinda', color: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500' },
  archived: { label: 'Arsiv', color: 'bg-amber-50 text-amber-700', dot: 'bg-amber-500' },
};

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08 } },
};

const fadeUp = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: 'easeOut' } },
};

const AVAILABLE_MODELS = [
  { provider: 'openai', model: 'gpt-5.4', label: 'GPT-5.4' },
  { provider: 'openai', model: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
  { provider: 'anthropic', model: 'claude-sonnet-4-6-20250414', label: 'Claude Sonnet 4.6' },
  { provider: 'anthropic', model: 'claude-opus-4-7-20250414', label: 'Claude Opus 4.7' },
  { provider: 'google', model: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
  { provider: 'google', model: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
];

export default function Dashboard() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [liveEvents, setLiveEvents] = useState<AgentEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [tenantId] = useState('default-tenant');
  const eventSourceRef = useRef<EventSource | null>(null);

  const [evolvingAgents, setEvolvingAgents] = useState<AgentInfo[]>([]);
  const [evolvingLoading, setEvolvingLoading] = useState(false);

  const [recentWorkflows, setRecentWorkflows] = useState<GlobalWorkflowItem[]>([]);
  const [wfTotal, setWfTotal] = useState(0);
  const [wfLoading, setWfLoading] = useState(false);

  const [showNewAgent, setShowNewAgent] = useState(false);
  const [newAgentName, setNewAgentName] = useState('');
  const [newAgentPurpose, setNewAgentPurpose] = useState('');
  const [newAgentModel, setNewAgentModel] = useState('');
  const [creating, setCreating] = useState(false);

  const loadRecentWorkflows = useCallback(async () => {
    setWfLoading(true);
    try {
      const { data } = await listAllWorkflows({ limit: 8 });
      setRecentWorkflows(data.workflows);
      setWfTotal(data.total);
    } catch (error) {
      console.error('Failed to load workflows:', error);
    } finally {
      setWfLoading(false);
    }
  }, []);

  const loadEvolvingAgents = useCallback(async () => {
    setEvolvingLoading(true);
    try {
      const res = await listEvolvingAgents(50);
      setEvolvingAgents(res.data);
    } catch (err) {
      console.error('Failed to load evolving agents:', err);
    } finally {
      setEvolvingLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    loadEvolvingAgents();
    loadRecentWorkflows();
    const es = dashboardApi.subscribeToEvents('', (event) => {
      setLiveEvents((prev) => [event, ...prev].slice(0, 30));
    });
    eventSourceRef.current = es;
    return () => { es.close(); };
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [agentsRes, overviewRes] = await Promise.allSettled([
        AgentBuilderAPI.listAgents(tenantId),
        dashboardApi.getOverview(tenantId),
      ]);
      if (agentsRes.status === 'fulfilled') setAgents((agentsRes.value || []) as Agent[]);
      if (overviewRes.status === 'fulfilled') setOverview(overviewRes.value);
    } catch (error) {
      console.error('Failed to load data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateAgent = async () => {
    if (!newAgentName.trim()) return;
    setCreating(true);
    try {
      const selected = AVAILABLE_MODELS.find(
        (m) => `${m.provider}/${m.model}` === newAgentModel,
      );
      const { data } = await createAgent(
        newAgentName.trim(),
        newAgentPurpose.trim(),
        selected?.provider,
        selected?.model,
      );
      setShowNewAgent(false);
      setNewAgentName('');
      setNewAgentPurpose('');
      setNewAgentModel('');
      navigate(`/agents/${data.agent_id}`);
    } catch (e) {
      console.error('Agent creation failed', e);
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-slate-50">
        <div className="flex flex-col items-center gap-3">
          <div className="animate-spin rounded-full h-10 w-10 border-2 border-blue-200 border-t-blue-600" />
          <span className="text-sm text-slate-400">Yukleniyor...</span>
        </div>
      </div>
    );
  }

  const s = overview?.summary;
  const allEvents = [...liveEvents, ...(overview?.recent_events || [])].slice(0, 20);

  return (
    <div className="min-h-screen bg-slate-50/80">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <Breadcrumb items={[{ label: 'Dashboard' }]} />

        {/* Header */}
        <div className="flex justify-between items-start mb-6">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">
              Agent Builder
            </h1>
            <p className="mt-0.5 text-sm text-slate-500">
              Akilli agentlar olusturun ve yonetin
            </p>
          </div>
          <button
            onClick={() => setShowNewAgent(true)}
            className="inline-flex items-center gap-1.5 px-4 py-2.5 bg-blue-600 text-white rounded-xl
                       hover:bg-blue-700 text-sm font-medium transition-all shadow-sm hover:shadow-md
                       active:scale-[0.97]"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Yeni Agent
          </button>
        </div>

        {/* Quick Actions */}
        <motion.div
          variants={stagger}
          initial="hidden"
          animate="show"
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-6"
        >
          <QuickAction
            onClick={() => navigate('/agents')}
            icon={<AgentIcon />}
            gradient="from-blue-500 to-blue-600"
            title="Agents"
            desc="Agent'lari olusturun ve yonetin"
          />
          <QuickAction
            onClick={() => navigate('/workflows')}
            icon={<WorkflowIcon />}
            gradient="from-violet-500 to-purple-600"
            title="Workflows"
            desc="Tum workflow'lari inceleyin"
          />
          <QuickAction
            onClick={() => navigate('/knowledges')}
            icon={<ResourceIcon />}
            gradient="from-emerald-500 to-teal-600"
            title="Knowledges"
            desc="Belgeler ve knowledge graph'ler"
          />
          <QuickAction
            onClick={() => navigate('/models')}
            icon={<EvolvingIcon />}
            gradient="from-amber-500 to-orange-600"
            title="Models"
            desc="LLM modelleri ve maliyet yonetimi"
          />
        </motion.div>

        {/* Stat Cards */}
        {s && (
          <motion.div
            variants={stagger}
            initial="hidden"
            animate="show"
            className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6"
          >
            <StatCard
              icon={<FolderIcon />}
              label="Workflow"
              value={wfTotal}
              sub={`${recentWorkflows.filter(w => w.status === 'published').length} yayinda`}
              accent="blue"
            />
            <StatCard
              icon={<DocStackIcon />}
              label="Toplam Belge"
              value={s.total_documents}
              sub={s.total_in_progress ? `${s.total_in_progress} isleniyor` : undefined}
              accent="slate"
            />
            <StatCard
              icon={<CheckCircleIcon />}
              label="Basarili"
              value={s.total_successful}
              sub={s.total_failed ? `${s.total_failed} hatali` : undefined}
              accent="emerald"
            />
            <StatCard
              icon={<ChartIcon />}
              label="Basari Orani"
              value={`%${s.overall_success_rate}`}
              sub={`${agents.length} agent`}
              accent="violet"
            />
          </motion.div>
        )}

        {/* Health + Active Batches */}
        <div className="flex flex-wrap items-center gap-2 mb-6">
          {overview?.health && Object.entries(overview.health).map(([key, val]) => (
            <span
              key={key}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border ${
                val.status === 'ok'
                  ? 'bg-emerald-50 text-emerald-700 border-emerald-100'
                  : val.status === 'error'
                  ? 'bg-red-50 text-red-700 border-red-100'
                  : 'bg-amber-50 text-amber-700 border-amber-100'
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${
                val.status === 'ok' ? 'bg-emerald-500' : val.status === 'error' ? 'bg-red-500' : 'bg-amber-500'
              }`} />
              {key}
            </span>
          ))}
        </div>

        {/* Active Batches */}
        {overview?.active_batches && overview.active_batches.length > 0 && (
          <div className="bg-white rounded-2xl border border-slate-200/80 p-5 mb-6 shadow-sm">
            <h2 className="text-xs font-semibold text-slate-500 mb-3 uppercase tracking-wider">
              Aktif Islemler
            </h2>
            <div className="space-y-3">
              {overview.active_batches.map((batch) => (
                <div key={batch.batch_job_id} className="flex items-center gap-4">
                  <span
                    className="text-sm font-medium text-slate-700 min-w-[120px] truncate"
                  >
                    {batch.workspace_id.slice(0, 15)}
                  </span>
                  <div className="flex-1">
                    <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
                      <motion.div
                        className="bg-gradient-to-r from-blue-500 to-blue-400 h-2 rounded-full"
                        initial={{ width: 0 }}
                        animate={{ width: `${Math.min(batch.percent_complete, 100)}%` }}
                        transition={{ duration: 0.8, ease: 'easeOut' }}
                      />
                    </div>
                  </div>
                  <span className="text-xs text-slate-500 min-w-[80px] text-right tabular-nums">
                    {batch.processed}/{batch.total} (%{batch.percent_complete})
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Evolving Agents */}
        <div className="bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden mb-6">
          <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-slate-800">Agent'lar</h2>
              {evolvingAgents.length > 0 && (
                <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                  {evolvingAgents.length}
                </span>
              )}
            </div>
            <button
              onClick={() => setShowNewAgent(true)}
              className="inline-flex items-center gap-1 text-xs text-amber-600 hover:text-amber-700 font-medium transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Yeni Agent
            </button>
          </div>

          {evolvingLoading ? (
            <div className="px-5 py-12 text-center">
              <div className="inline-block animate-spin rounded-full h-5 w-5 border-2 border-amber-200 border-t-amber-600" />
            </div>
          ) : evolvingAgents.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 gap-2">
              <div className="w-10 h-10 rounded-full bg-amber-50 flex items-center justify-center">
                <EvolvingIcon className="w-5 h-5 text-amber-500" />
              </div>
              <span className="text-sm text-slate-400">Henuz agent yok</span>
              <button
                onClick={() => setShowNewAgent(true)}
                className="text-xs text-amber-600 hover:text-amber-700 font-medium"
              >
                Ilk agent'inizi olusturun &rarr;
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50/70">
                    <th className="text-left px-5 py-2.5 text-xs font-medium text-slate-500">Agent</th>
                    <th className="text-left px-3 py-2.5 text-xs font-medium text-slate-500">Domain</th>
                    <th className="text-right px-3 py-2.5 text-xs font-medium text-slate-500">Entity</th>
                    <th className="text-right px-3 py-2.5 text-xs font-medium text-slate-500">Iliski</th>
                    <th className="text-left px-3 py-2.5 text-xs font-medium text-slate-500">Durum</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {evolvingAgents.map((ag) => (
                    <tr
                      key={ag.agent_id}
                      onClick={() => navigate(`/agents/${ag.agent_id}`)}
                      className="hover:bg-amber-50/40 cursor-pointer transition-colors group"
                    >
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2.5">
                          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center text-white text-xs font-bold shrink-0">
                            {ag.name?.[0]?.toUpperCase() || 'A'}
                          </div>
                          <div>
                            <span className="font-medium text-slate-800 group-hover:text-amber-700 transition-colors">
                              {ag.name || ag.agent_id.slice(0, 12)}
                            </span>
                            {ag.purpose && (
                              <p className="text-[11px] text-slate-400 truncate max-w-[200px]">{ag.purpose}</p>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-3 text-slate-600 text-xs">{ag.domain || '-'}</td>
                      <td className="px-3 py-3 text-right text-slate-600 tabular-nums">{ag.entity_count}</td>
                      <td className="px-3 py-3 text-right text-slate-600 tabular-nums">{ag.relationship_count}</td>
                      <td className="px-3 py-3">
                        <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium ${
                          ag.is_empty
                            ? 'bg-slate-100 text-slate-600'
                            : 'bg-emerald-50 text-emerald-700'
                        }`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${ag.is_empty ? 'bg-slate-400' : 'bg-emerald-500'}`} />
                          {ag.is_empty ? 'Bos' : 'Aktif'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Two-Column: Workflows Table + Activity */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Workflows Table */}
          <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold text-slate-800">Workflow'lar</h2>
                {wfTotal > 0 && (
                  <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                    {wfTotal}
                  </span>
                )}
              </div>
              <Link
                to="/workflows"
                className="text-xs text-blue-600 hover:text-blue-700 font-medium transition-colors"
              >
                Tumunu gor &rarr;
              </Link>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50/70">
                    <th className="text-left px-5 py-2.5 text-xs font-medium text-slate-500">Workflow</th>
                    <th className="text-left px-3 py-2.5 text-xs font-medium text-slate-500">Agent</th>
                    <th className="text-right px-3 py-2.5 text-xs font-medium text-slate-500">Versiyon</th>
                    <th className="text-left px-3 py-2.5 text-xs font-medium text-slate-500">Durum</th>
                    <th className="text-left px-3 py-2.5 text-xs font-medium text-slate-500 hidden lg:table-cell">Tarih</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {wfLoading ? (
                    <tr>
                      <td colSpan={5} className="px-5 py-16 text-center">
                        <div className="inline-block animate-spin rounded-full h-5 w-5 border-2 border-blue-200 border-t-blue-600" />
                      </td>
                    </tr>
                  ) : recentWorkflows.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-5 py-16 text-center">
                        <div className="flex flex-col items-center gap-2">
                          <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center">
                            <FolderIcon className="w-5 h-5 text-slate-400" />
                          </div>
                          <span className="text-sm text-slate-400">Henuz workflow yok</span>
                          <button
                            onClick={() => navigate('/agents')}
                            className="text-xs text-blue-600 hover:text-blue-700 font-medium"
                          >
                            Agent olusturarak baslayin &rarr;
                          </button>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    recentWorkflows.map((wf) => {
                      const st = WF_STATUS_LABELS[wf.status] || { label: wf.status, color: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400' };
                      return (
                        <tr
                          key={wf.workflow_id}
                          onClick={async () => {
                            if (!wf.agent_id) return;
                            try { await setActiveWorkflow(wf.agent_id, wf.workflow_id); } catch {}
                            navigate(`/agents/${wf.agent_id}/workflow`);
                          }}
                          className={`${wf.agent_id ? 'hover:bg-blue-50/40 cursor-pointer' : ''} transition-colors group`}
                        >
                          <td className="px-5 py-3">
                            <div>
                              <span className="font-medium text-slate-800 group-hover:text-blue-700 transition-colors">
                                {wf.name}
                              </span>
                              {wf.is_template && (
                                <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 font-medium">T</span>
                              )}
                            </div>
                            {wf.description && (
                              <p className="text-xs text-slate-400 truncate max-w-[200px]">{wf.description}</p>
                            )}
                          </td>
                          <td className="px-3 py-3 text-xs text-slate-500 truncate max-w-[120px]">{wf.agent_name}</td>
                          <td className="px-3 py-3 text-right text-slate-600 tabular-nums">v{wf.version}</td>
                          <td className="px-3 py-3">
                            <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium ${st.color}`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                              {st.label}
                            </span>
                          </td>
                          <td className="px-3 py-3 text-xs text-slate-400 whitespace-nowrap hidden lg:table-cell">
                            {wf.updated_at ? new Date(wf.updated_at).toLocaleDateString('tr-TR') : '-'}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Activity Feed */}
          <div className="bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden flex flex-col">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold text-slate-800">Aktivite</h2>
                {liveEvents.length > 0 && (
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                  </span>
                )}
              </div>
            </div>
            <div className="flex-1 max-h-[460px] overflow-y-auto">
              <AnimatePresence initial={false}>
                {allEvents.map((ev, i) => {
                  const info = EVENT_LABELS[ev.event_type] || { label: ev.event_type, icon: '·', color: 'text-slate-400' };
                  return (
                    <motion.div
                      key={`${ev.event_id}-${i}`}
                      initial={{ opacity: 0, x: 12 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0, x: -12 }}
                      transition={{ duration: 0.25 }}
                      className="px-4 py-2.5 border-b border-slate-50 last:border-0 hover:bg-slate-50/50 transition-colors"
                    >
                      <div className="flex items-start gap-2.5">
                        <span className={`mt-0.5 text-sm font-mono leading-none ${info.color}`}>{info.icon}</span>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-medium text-slate-700 leading-tight">{info.label}</p>
                          <div className="flex items-center gap-1.5 mt-0.5">
                            {ev.source_agent && (
                              <span className="text-[10px] text-slate-400 truncate max-w-[100px]">{ev.source_agent}</span>
                            )}
                            <span className="text-[10px] text-slate-300">
                              {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString('tr-TR') : ''}
                            </span>
                          </div>
                        </div>
                      </div>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
              {allEvents.length === 0 && (
                <div className="flex flex-col items-center justify-center py-16 gap-2">
                  <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center">
                    <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <span className="text-xs text-slate-400">Henuz aktivite yok</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* New Agent Modal */}
      <AnimatePresence>
        {showNewAgent && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
            onClick={() => !creating && setShowNewAgent(false)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 10 }}
              transition={{ duration: 0.2 }}
              className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="px-6 py-5 border-b border-slate-100">
                <h2 className="text-lg font-semibold text-slate-900">Yeni Agent Olustur</h2>
                <p className="text-sm text-slate-500 mt-0.5">Agent bilgilerini girin</p>
              </div>

              <div className="px-6 py-5 space-y-4">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">
                    Agent Adi <span className="text-red-400">*</span>
                  </label>
                  <input
                    type="text"
                    value={newAgentName}
                    onChange={(e) => setNewAgentName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleCreateAgent()}
                    placeholder="orn. Ticaret Sicil Gazeteleri"
                    autoFocus
                    className="w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm
                               focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400
                               placeholder:text-slate-400 transition-all"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">
                    Amac / Aciklama
                  </label>
                  <textarea
                    value={newAgentPurpose}
                    onChange={(e) => setNewAgentPurpose(e.target.value)}
                    placeholder="Agent'in ne yapacagini kisa bir sekilde aciklayiniz..."
                    rows={3}
                    className="w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm resize-none
                               focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400
                               placeholder:text-slate-400 transition-all"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">
                    LLM Model
                  </label>
                  <select
                    value={newAgentModel}
                    onChange={(e) => setNewAgentModel(e.target.value)}
                    className="w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm
                               focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400
                               bg-white transition-all"
                  >
                    <option value="">Sistem Varsayilani</option>
                    {AVAILABLE_MODELS.map((m) => (
                      <option key={`${m.provider}/${m.model}`} value={`${m.provider}/${m.model}`}>
                        {m.label} ({m.provider})
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-slate-400 mt-1">Bos birakilirsa sistem varsayilani kullanilir</p>
                </div>
              </div>

              <div className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex items-center justify-end gap-2.5">
                <button
                  onClick={() => { setShowNewAgent(false); setNewAgentName(''); setNewAgentPurpose(''); setNewAgentModel(''); }}
                  disabled={creating}
                  className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800
                             hover:bg-slate-100 rounded-xl transition-colors disabled:opacity-50"
                >
                  Iptal
                </button>
                <button
                  onClick={handleCreateAgent}
                  disabled={!newAgentName.trim() || creating}
                  className="inline-flex items-center gap-1.5 px-5 py-2 bg-blue-600 text-white rounded-xl
                             text-sm font-medium hover:bg-blue-700 transition-all shadow-sm
                             disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.97]"
                >
                  {creating && (
                    <div className="animate-spin rounded-full h-3.5 w-3.5 border-2 border-white/30 border-t-white" />
                  )}
                  Olustur
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ─── Sub-components ─── */

function StatCard({ icon, label, value, sub, accent }: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  sub?: string;
  accent: string;
}) {
  const accents: Record<string, { bg: string; iconBg: string; iconText: string }> = {
    blue: { bg: 'bg-white', iconBg: 'bg-blue-50', iconText: 'text-blue-600' },
    slate: { bg: 'bg-white', iconBg: 'bg-slate-100', iconText: 'text-slate-600' },
    emerald: { bg: 'bg-white', iconBg: 'bg-emerald-50', iconText: 'text-emerald-600' },
    violet: { bg: 'bg-white', iconBg: 'bg-violet-50', iconText: 'text-violet-600' },
  };
  const a = accents[accent] || accents.slate;

  return (
    <motion.div
      variants={fadeUp}
      className={`${a.bg} rounded-2xl border border-slate-200/80 p-4 shadow-sm
                  hover:shadow-md transition-shadow`}
    >
      <div className="flex items-center justify-between mb-3">
        <div className={`${a.iconBg} ${a.iconText} rounded-xl p-2.5`}>{icon}</div>
      </div>
      <div className="text-2xl font-bold text-slate-900 tracking-tight">{String(value)}</div>
      <div className="text-xs text-slate-500 mt-0.5 font-medium">{label}</div>
      {sub && <div className="text-[11px] text-slate-400 mt-0.5">{sub}</div>}
    </motion.div>
  );
}

function QuickAction({ onClick, icon, gradient, title, desc }: {
  onClick?: () => void;
  icon: React.ReactNode;
  gradient: string;
  title: string;
  desc: string;
}) {
  return (
    <motion.div
      variants={fadeUp}
      whileHover={{ y: -2 }}
      whileTap={{ scale: 0.98 }}
      onClick={onClick}
      className={`bg-gradient-to-br ${gradient} rounded-2xl p-4 cursor-pointer
                  shadow-sm hover:shadow-lg transition-shadow text-white`}
    >
      <div className="flex items-center gap-3">
        <div className="bg-white/20 rounded-xl p-2.5 backdrop-blur-sm">{icon}</div>
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="text-xs text-white/70 mt-0.5">{desc}</p>
        </div>
      </div>
    </motion.div>
  );
}

/* ─── Icons ─── */

function WorkflowIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
    </svg>
  );
}

function AgentIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
    </svg>
  );
}

function ResourceIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
    </svg>
  );
}

function FolderIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
    </svg>
  );
}

function DocStackIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  );
}

function CheckCircleIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  );
}

function ChartIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
    </svg>
  );
}

function EvolvingIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
    </svg>
  );
}
