import { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { AgentBuilderAPI, Agent as ApiAgent } from '../services/agentBuilderApi';
import {
  dashboardApi,
  DashboardOverview,
  PaginatedWorkspaces,
  AgentEvent,
} from '../services/workspaceApi';
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

const PAGE_SIZE_OPTIONS = [5, 10, 20, 50];

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08 } },
};

const fadeUp = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: 'easeOut' } },
};

export default function Dashboard() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [liveEvents, setLiveEvents] = useState<AgentEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [tenantId] = useState('default-tenant');
  const eventSourceRef = useRef<EventSource | null>(null);

  const [wsPage, setWsPage] = useState(1);
  const [wsPageSize, setWsPageSize] = useState(10);
  const [wsPaginated, setWsPaginated] = useState<PaginatedWorkspaces | null>(null);
  const [wsLoading, setWsLoading] = useState(false);

  const loadWorkspaces = useCallback(async (page: number, pageSize: number) => {
    setWsLoading(true);
    try {
      const data = await dashboardApi.getWorkspacesPaginated(tenantId, page, pageSize);
      setWsPaginated(data);
    } catch (error) {
      console.error('Failed to load workspaces:', error);
    } finally {
      setWsLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    loadData();
    loadWorkspaces(1, wsPageSize);
    const es = dashboardApi.subscribeToEvents('', (event) => {
      setLiveEvents((prev) => [event, ...prev].slice(0, 30));
    });
    eventSourceRef.current = es;
    return () => { es.close(); };
  }, []);

  useEffect(() => {
    loadWorkspaces(wsPage, wsPageSize);
  }, [wsPage, wsPageSize, loadWorkspaces]);

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
              Knowledge Base Builder
            </h1>
            <p className="mt-0.5 text-sm text-slate-500">
              Belgelerinizden bilgi grafigi olusturun
            </p>
          </div>
          <button
            onClick={() => navigate('/workspaces')}
            className="inline-flex items-center gap-1.5 px-4 py-2.5 bg-blue-600 text-white rounded-xl
                       hover:bg-blue-700 text-sm font-medium transition-all shadow-sm hover:shadow-md
                       active:scale-[0.97]"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Yeni Workspace
          </button>
        </div>

        {/* Quick Actions */}
        <motion.div
          variants={stagger}
          initial="hidden"
          animate="show"
          className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6"
        >
          <QuickAction
            onClick={() => navigate('/workspaces')}
            icon={<WorkspaceIcon />}
            gradient="from-blue-500 to-blue-600"
            title="Workspace'ler"
            desc="Belge koleksiyonlarini yonetin"
          />
          <QuickAction
            onClick={() => navigate('/chat-agents')}
            icon={<AgentIcon />}
            gradient="from-violet-500 to-purple-600"
            title="Chat Agent"
            desc="Agent olusturun ve yonetin"
          />
          <QuickAction
            onClick={() => navigate('/resources')}
            icon={<ResourceIcon />}
            gradient="from-emerald-500 to-teal-600"
            title="Resource'lar"
            desc="Kaynaklari duzenleyin"
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
              label="Workspace"
              value={s.workspace_count}
              sub={`${s.active_workspaces} aktif`}
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
                  <Link
                    to={`/workspaces/${batch.workspace_id}`}
                    className="text-sm font-medium text-blue-600 hover:text-blue-700 min-w-[120px] truncate"
                  >
                    {batch.workspace_id.slice(0, 15)}
                  </Link>
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

        {/* Two-Column: Workspace Table + Activity */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Workspace Table */}
          <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold text-slate-800">Workspace'ler</h2>
                {wsPaginated && (
                  <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                    {wsPaginated.total}
                  </span>
                )}
              </div>
              <Link
                to="/workspaces"
                className="text-xs text-blue-600 hover:text-blue-700 font-medium transition-colors"
              >
                Tumunu gor &rarr;
              </Link>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50/70">
                    <th className="text-left px-5 py-2.5 text-xs font-medium text-slate-500">Ad</th>
                    <th className="text-left px-3 py-2.5 text-xs font-medium text-slate-500">Durum</th>
                    <th className="text-right px-3 py-2.5 text-xs font-medium text-slate-500">Belge</th>
                    <th className="text-right px-3 py-2.5 text-xs font-medium text-slate-500 hidden sm:table-cell">Islenen</th>
                    <th className="text-right px-3 py-2.5 text-xs font-medium text-slate-500 hidden md:table-cell">Basarili</th>
                    <th className="text-right px-3 py-2.5 text-xs font-medium text-slate-500 hidden md:table-cell">Hatali</th>
                    <th className="text-left px-3 py-2.5 text-xs font-medium text-slate-500">Basari</th>
                    <th className="text-left px-3 py-2.5 text-xs font-medium text-slate-500 hidden lg:table-cell">Tarih</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {wsLoading && !wsPaginated ? (
                    <tr>
                      <td colSpan={8} className="px-5 py-16 text-center">
                        <div className="inline-block animate-spin rounded-full h-5 w-5 border-2 border-blue-200 border-t-blue-600" />
                      </td>
                    </tr>
                  ) : !wsPaginated || wsPaginated.items.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-5 py-16 text-center">
                        <div className="flex flex-col items-center gap-2">
                          <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center">
                            <FolderIcon className="w-5 h-5 text-slate-400" />
                          </div>
                          <span className="text-sm text-slate-400">Henuz workspace yok</span>
                          <button
                            onClick={() => navigate('/workspaces')}
                            className="text-xs text-blue-600 hover:text-blue-700 font-medium"
                          >
                            Ilk workspace'inizi olusturun &rarr;
                          </button>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    wsPaginated.items.map((ws) => {
                      const st = STATUS_LABELS[ws.status] || { label: ws.status, color: 'bg-slate-100 text-slate-600', dot: 'bg-slate-400' };
                      return (
                        <tr
                          key={ws.id}
                          onClick={() => navigate(`/workspaces/${ws.id}`)}
                          className="hover:bg-blue-50/40 cursor-pointer transition-colors group"
                        >
                          <td className="px-5 py-3">
                            <span className="font-medium text-slate-800 group-hover:text-blue-700 transition-colors">
                              {ws.name}
                            </span>
                          </td>
                          <td className="px-3 py-3">
                            <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium ${st.color}`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                              {st.label}
                            </span>
                          </td>
                          <td className="px-3 py-3 text-right text-slate-600 tabular-nums">{ws.document_count}</td>
                          <td className="px-3 py-3 text-right text-slate-600 tabular-nums hidden sm:table-cell">{ws.processed}</td>
                          <td className="px-3 py-3 text-right text-emerald-600 tabular-nums hidden md:table-cell">{ws.successful}</td>
                          <td className="px-3 py-3 text-right text-red-500 tabular-nums hidden md:table-cell">{ws.failed}</td>
                          <td className="px-3 py-3">
                            {ws.document_count > 0 ? (
                              <div className="flex items-center gap-2">
                                <div className="w-14 bg-slate-100 rounded-full h-1.5 overflow-hidden">
                                  <div
                                    className="bg-emerald-500 h-1.5 rounded-full transition-all duration-500"
                                    style={{ width: `${Math.min(ws.success_rate, 100)}%` }}
                                  />
                                </div>
                                <span className="text-xs text-slate-500 tabular-nums">%{ws.success_rate}</span>
                              </div>
                            ) : (
                              <span className="text-xs text-slate-300">&mdash;</span>
                            )}
                          </td>
                          <td className="px-3 py-3 text-xs text-slate-400 whitespace-nowrap hidden lg:table-cell">
                            {ws.created_at ? new Date(ws.created_at).toLocaleDateString('tr-TR') : '-'}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {wsPaginated && wsPaginated.total > 0 && (
              <div className="px-5 py-3 border-t border-slate-100 flex items-center justify-between">
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <select
                    value={wsPageSize}
                    onChange={(e) => { setWsPageSize(Number(e.target.value)); setWsPage(1); }}
                    className="border border-slate-200 rounded-lg px-2 py-1 text-xs bg-white
                               focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none"
                  >
                    {PAGE_SIZE_OPTIONS.map((sz) => (
                      <option key={sz} value={sz}>{sz}</option>
                    ))}
                  </select>
                  <span className="text-slate-400">
                    {((wsPage - 1) * wsPageSize) + 1}&ndash;{Math.min(wsPage * wsPageSize, wsPaginated.total)} / {wsPaginated.total}
                  </span>
                </div>
                <div className="flex items-center gap-1">
                  <PagBtn onClick={() => setWsPage(1)} disabled={wsPage <= 1}>&laquo;</PagBtn>
                  <PagBtn onClick={() => setWsPage((p) => Math.max(1, p - 1))} disabled={wsPage <= 1}>&lsaquo;</PagBtn>
                  {Array.from({ length: wsPaginated.total_pages }, (_, i) => i + 1)
                    .filter((p) => p === 1 || p === wsPaginated.total_pages || Math.abs(p - wsPage) <= 1)
                    .reduce<(number | 'ellipsis')[]>((acc, p, i, arr) => {
                      if (i > 0 && p - (arr[i - 1]) > 1) acc.push('ellipsis');
                      acc.push(p);
                      return acc;
                    }, [])
                    .map((p, i) =>
                      p === 'ellipsis' ? (
                        <span key={`e${i}`} className="px-1 text-xs text-slate-300">&hellip;</span>
                      ) : (
                        <PagBtn
                          key={p}
                          onClick={() => setWsPage(p)}
                          active={p === wsPage}
                        >
                          {p}
                        </PagBtn>
                      ),
                    )}
                  <PagBtn onClick={() => setWsPage((p) => Math.min(wsPaginated.total_pages, p + 1))} disabled={wsPage >= wsPaginated.total_pages}>&rsaquo;</PagBtn>
                  <PagBtn onClick={() => setWsPage(wsPaginated.total_pages)} disabled={wsPage >= wsPaginated.total_pages}>&raquo;</PagBtn>
                </div>
              </div>
            )}
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

function PagBtn({ onClick, disabled, active, children }: {
  onClick?: () => void;
  disabled?: boolean;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`px-2.5 py-1 text-xs rounded-lg border transition-colors ${
        active
          ? 'bg-blue-600 text-white border-blue-600'
          : 'border-slate-200 hover:bg-slate-50 text-slate-600 disabled:opacity-30 disabled:cursor-not-allowed'
      }`}
    >
      {children}
    </button>
  );
}

/* ─── Icons ─── */

function WorkspaceIcon() {
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
