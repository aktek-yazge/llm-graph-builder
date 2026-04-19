import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listAllWorkflows,
  deleteWorkflowApi,
  deleteGlobalWorkflow,
  shareWorkflowAsTemplate,
  setActiveWorkflow,
  createGlobalWorkflow,
  listAgents as listEvolvingAgents,
  type GlobalWorkflowItem,
  type AgentInfo,
} from '../services/evolvingApi';

const STATUS_STYLES: Record<string, { label: string; bg: string; text: string }> = {
  draft: { label: 'Draft', bg: 'bg-gray-100', text: 'text-gray-600' },
  published: { label: 'Published', bg: 'bg-emerald-50', text: 'text-emerald-700' },
  archived: { label: 'Archived', bg: 'bg-orange-50', text: 'text-orange-600' },
};

export default function WorkflowsPage() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<GlobalWorkflowItem[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const PAGE_SIZE = 20;

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newAgentId, setNewAgentId] = useState('');
  const [creating, setCreating] = useState(false);
  const [agents, setAgents] = useState<AgentInfo[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listAllWorkflows({
        search: search || undefined,
        status: statusFilter || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setWorkflows(resp.data.workflows || []);
      setTotal(resp.data.total || 0);
    } catch {
      setWorkflows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [search, statusFilter, page]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setPage(0);
  }, [search, statusFilter]);

  const openCreateModal = async () => {
    setShowCreate(true);
    try {
      const res = await listEvolvingAgents(100);
      setAgents(res.data);
    } catch {
      setAgents([]);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const { data } = await createGlobalWorkflow(
        newName.trim(),
        newDesc.trim(),
        newAgentId || undefined,
      );
      setShowCreate(false);
      setNewName('');
      setNewDesc('');
      setNewAgentId('');
      if (data.agent_id) {
        try { await setActiveWorkflow(data.agent_id, data.workflow_id); } catch {}
        navigate(`/agents/${data.agent_id}/workflow`);
      } else {
        load();
      }
    } catch {
      alert('Workflow olusturulamadi');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (wf: GlobalWorkflowItem) => {
    if (!confirm(`'${wf.name}' workflow'unu silmek istediginize emin misiniz?`)) return;
    try {
      if (wf.agent_id) {
        await deleteWorkflowApi(wf.agent_id, wf.workflow_id);
      } else {
        await deleteGlobalWorkflow(wf.workflow_id);
      }
      load();
    } catch {
      alert('Silme hatasi');
    }
  };

  const handleShare = async (wf: GlobalWorkflowItem) => {
    try {
      await shareWorkflowAsTemplate(wf.agent_id, wf.workflow_id);
      load();
    } catch {
      alert('Paylasim hatasi');
    }
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="max-w-7xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Workflows</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Tum agent'lardaki workflow'lari inceleyin ve yonetin
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-400">
            <span className="font-medium text-gray-900">{total}</span> workflow
          </span>
          <button
            onClick={openCreateModal}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-lg
                       hover:bg-blue-700 text-sm font-medium transition-all shadow-sm hover:shadow-md
                       active:scale-[0.97]"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Yeni Workflow
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 mb-5">
        <div className="relative flex-1 max-w-sm">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            placeholder="Workflow ara..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-gray-200 focus:border-gray-300 transition-all"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="text-sm border border-gray-200 rounded-lg px-3 py-2 text-gray-600 focus:outline-none focus:ring-2 focus:ring-gray-200 bg-white"
        >
          <option value="">Tum Durumlar</option>
          <option value="draft">Draft</option>
          <option value="published">Published</option>
          <option value="archived">Archived</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-100 rounded-xl overflow-hidden shadow-sm">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100">
              <th className="text-left text-xs font-medium text-gray-400 uppercase tracking-wider px-5 py-3">Workflow</th>
              <th className="text-left text-xs font-medium text-gray-400 uppercase tracking-wider px-5 py-3">Agent</th>
              <th className="text-center text-xs font-medium text-gray-400 uppercase tracking-wider px-4 py-3">Versiyon</th>
              <th className="text-center text-xs font-medium text-gray-400 uppercase tracking-wider px-4 py-3">Node</th>
              <th className="text-center text-xs font-medium text-gray-400 uppercase tracking-wider px-4 py-3">Durum</th>
              <th className="text-center text-xs font-medium text-gray-400 uppercase tracking-wider px-4 py-3">Template</th>
              <th className="text-left text-xs font-medium text-gray-400 uppercase tracking-wider px-5 py-3">Guncelleme</th>
              <th className="text-right text-xs font-medium text-gray-400 uppercase tracking-wider px-5 py-3">Islemler</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="text-center py-12 text-sm text-gray-400">
                  Yukleniyor...
                </td>
              </tr>
            ) : workflows.length === 0 ? (
              <tr>
                <td colSpan={8} className="text-center py-12">
                  <div className="text-gray-400 text-sm">
                    {search || statusFilter ? 'Sonuc bulunamadi' : 'Henuz workflow olusturulmamis'}
                  </div>
                  <p className="text-gray-300 text-xs mt-1">
                    Yeni Workflow butonuyla olusturabilirsiniz
                  </p>
                </td>
              </tr>
            ) : (
              workflows.map((wf) => {
                const st = STATUS_STYLES[wf.status] || STATUS_STYLES.draft;
                const isStandalone = !wf.agent_id;
                const openWorkflow = async () => {
                  if (isStandalone) return;
                  try {
                    await setActiveWorkflow(wf.agent_id, wf.workflow_id);
                  } catch { /* best effort */ }
                  navigate(`/agents/${wf.agent_id}/workflow`);
                };
                return (
                  <tr
                    key={wf.workflow_id}
                    className={`border-b border-gray-50 hover:bg-gray-50/50 transition-colors ${isStandalone ? '' : 'cursor-pointer'}`}
                    onClick={openWorkflow}
                  >
                    <td className="px-5 py-3.5">
                      <div className="font-medium text-sm text-gray-900">{wf.name}</div>
                      {wf.description && (
                        <div className="text-xs text-gray-400 mt-0.5 line-clamp-1 max-w-xs">{wf.description}</div>
                      )}
                    </td>
                    <td className="px-5 py-3.5">
                      {isStandalone ? (
                        <span className="inline-block text-[11px] font-medium px-2 py-0.5 rounded-full bg-slate-100 text-slate-500">
                          Standalone
                        </span>
                      ) : (
                        <>
                          <div className="text-sm text-gray-600">{wf.agent_name}</div>
                          <div className="text-[10px] text-gray-300 font-mono">{wf.agent_id?.slice(0, 15)}</div>
                        </>
                      )}
                    </td>
                    <td className="px-4 py-3.5 text-center">
                      <span className="text-sm font-medium text-gray-700">v{wf.version}</span>
                    </td>
                    <td className="px-4 py-3.5 text-center">
                      <span className="text-sm text-gray-600">{wf.node_count}</span>
                    </td>
                    <td className="px-4 py-3.5 text-center">
                      <span className={`inline-block text-[11px] font-medium px-2 py-0.5 rounded-full ${st.bg} ${st.text}`}>
                        {st.label}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-center">
                      {wf.is_template ? (
                        <span className="inline-block text-[11px] font-medium px-2 py-0.5 rounded-full bg-purple-50 text-purple-600">
                          Paylasilan
                        </span>
                      ) : (
                        <span className="text-gray-300 text-xs">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3.5">
                      <div className="text-xs text-gray-400">
                        {wf.updated_at ? new Date(wf.updated_at).toLocaleDateString('tr-TR', {
                          day: 'numeric',
                          month: 'short',
                          year: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        }) : '—'}
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                        {!isStandalone && (
                          <button
                            onClick={() => {
                              setActiveWorkflow(wf.agent_id, wf.workflow_id).catch(() => {});
                              navigate(`/agents/${wf.agent_id}/workflow`);
                            }}
                            className="text-xs text-gray-500 hover:text-gray-800 px-2 py-1 rounded hover:bg-gray-100 transition-colors"
                            title="Workflow editor'u ac"
                          >
                            Ac
                          </button>
                        )}
                        {!wf.is_template && wf.agent_id && (
                          <button
                            onClick={() => handleShare(wf)}
                            className="text-xs text-blue-500 hover:text-blue-700 px-2 py-1 rounded hover:bg-blue-50 transition-colors"
                            title="Template olarak paylas"
                          >
                            Paylas
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(wf)}
                          className="text-xs text-red-400 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50 transition-colors"
                          title="Sil"
                        >
                          Sil
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-gray-100">
            <span className="text-xs text-gray-400">
              {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} / {total}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="text-xs text-gray-500 hover:text-gray-800 px-2 py-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Onceki
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="text-xs text-gray-500 hover:text-gray-800 px-2 py-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                Sonraki
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Create Workflow Modal */}
      {showCreate && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
          onClick={() => !creating && setShowCreate(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-6 py-5 border-b border-slate-100">
              <h2 className="text-lg font-semibold text-slate-900">Yeni Workflow Olustur</h2>
              <p className="text-sm text-slate-500 mt-0.5">Workflow bilgilerini girin</p>
            </div>

            <div className="px-6 py-5 space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Workflow Adi <span className="text-red-400">*</span>
                </label>
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                  placeholder="orn. Belge Isleme Pipeline"
                  autoFocus
                  className="w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm
                             focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400
                             placeholder:text-slate-400 transition-all"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Aciklama
                </label>
                <textarea
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="Workflow'un ne yapacagini kisa bir sekilde aciklayiniz..."
                  rows={2}
                  className="w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm resize-none
                             focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400
                             placeholder:text-slate-400 transition-all"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  Agent <span className="text-slate-400 font-normal">(opsiyonel)</span>
                </label>
                <select
                  value={newAgentId}
                  onChange={(e) => setNewAgentId(e.target.value)}
                  className="w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm bg-white
                             focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400
                             transition-all"
                >
                  <option value="">Standalone (agent'a bagli degil)</option>
                  {agents.map((a) => (
                    <option key={a.agent_id} value={a.agent_id}>
                      {a.name || a.agent_id}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex items-center justify-end gap-2.5">
              <button
                onClick={() => { setShowCreate(false); setNewName(''); setNewDesc(''); setNewAgentId(''); }}
                disabled={creating}
                className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800
                           hover:bg-slate-100 rounded-xl transition-colors disabled:opacity-50"
              >
                Iptal
              </button>
              <button
                onClick={handleCreate}
                disabled={!newName.trim() || creating}
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
          </div>
        </div>
      )}
    </div>
  );
}
