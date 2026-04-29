import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  listAgents,
  createAgent,
  deleteAgent,
  restoreAgent as apiRestore,
  purgeAgent as apiPurge,
  listDeletedAgents,
  type AgentInfo,
  type DeletedAgentInfo,
} from '../services/evolvingApi';
import Breadcrumb from '../components/Breadcrumb';

const AVAILABLE_MODELS = [
  { provider: 'openai', model: 'gpt-5.4', label: 'GPT-5.4' },
  { provider: 'openai', model: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
  { provider: 'anthropic', model: 'claude-sonnet-4-6-20250414', label: 'Claude Sonnet 4.6' },
  { provider: 'anthropic', model: 'claude-opus-4-7-20250414', label: 'Claude Opus 4.7' },
  { provider: 'google', model: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
  { provider: 'google', model: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
];

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06 } },
};

const fadeUp = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } },
};

function formatDeletedAt(iso: string | null): string {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('tr-TR', { dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return iso;
  }
}

export default function AgentListPage() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newPurpose, setNewPurpose] = useState('');
  const [newModel, setNewModel] = useState('');
  const [creating, setCreating] = useState(false);

  const [deletedAgents, setDeletedAgents] = useState<DeletedAgentInfo[]>([]);
  const [trashOpen, setTrashOpen] = useState(false);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listAgents(50);
      setAgents(res.data);
    } catch (err) {
      console.error('Failed to load agents:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDeleted = useCallback(async () => {
    try {
      const res = await listDeletedAgents();
      setDeletedAgents(res.data.deleted || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    loadAgents();
    loadDeleted();
  }, [loadAgents, loadDeleted]);

  const handleCreate = async () => {
    if (!newName.trim() || creating) return;
    setCreating(true);
    try {
      const sel = AVAILABLE_MODELS.find((m) => `${m.provider}/${m.model}` === newModel);
      const res = await createAgent(newName.trim(), newPurpose.trim(), sel?.provider, sel?.model);
      setShowCreate(false);
      setNewName('');
      setNewPurpose('');
      setNewModel('');
      navigate(`/agents/${res.data.agent_id}`);
    } catch {
      window.alert('Agent olusturulamadi. Lutfen tekrar deneyin.');
    } finally {
      setCreating(false);
    }
  };

  const handleSoftDelete = async (e: React.MouseEvent, agentId: string) => {
    e.stopPropagation();
    if (window.confirm('Agent cop kutusuna tasinacak. Devam edilsin mi?')) {
      try {
        await deleteAgent(agentId, false);
        setAgents((prev) => prev.filter((a) => a.agent_id !== agentId));
        await loadDeleted();
        setTrashOpen(true);
      } catch (err) {
        console.error('Silinemedi:', err);
      }
    }
  };

  const handleRestore = async (agentId: string) => {
    try {
      await apiRestore(agentId);
      await loadAgents();
      await loadDeleted();
    } catch (err) {
      console.error('Geri yuklenemedi:', err);
    }
  };

  const handlePurge = async (agentId: string, name: string) => {
    if (
      window.confirm(
        `"${name}" KALICI olarak silinecek. Tum veriler geri getirilemez.\n\nDevam edilsin mi?`,
      )
    ) {
      try {
        await apiPurge(agentId);
        await loadDeleted();
      } catch (err) {
        console.error('Kalici silinemedi:', err);
      }
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-6">
        <Breadcrumb items={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Agents' }]} />

        {/* Header */}
        <div className="flex items-center justify-between mt-4 mb-6">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Agent'lar</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              Agent'larinizi yonetin, yeni agent olusturun veya mevcut bir agent ile sohbet baslatin.
            </p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-xl hover:bg-blue-700 transition-colors"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
            Yeni Agent
          </button>
        </div>

        {/* Agent Cards */}
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="animate-spin rounded-full h-8 w-8 border-2 border-blue-200 border-t-blue-600" />
          </div>
        ) : agents.length === 0 ? (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex flex-col items-center justify-center py-20 text-center"
          >
            <div className="w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center mb-4">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a4 4 0 0 1 4 4v2a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4z" />
                <path d="M16 14H8a4 4 0 0 0-4 4v2h16v-2a4 4 0 0 0-4-4z" />
              </svg>
            </div>
            <p className="text-base font-medium text-slate-600">Henuz agent yok</p>
            <p className="text-sm text-slate-400 mt-1">
              Ilk agent'inizi olusturarak baslayabilirsiniz.
            </p>
            <button
              onClick={() => setShowCreate(true)}
              className="mt-4 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-xl hover:bg-blue-700 transition-colors"
            >
              Agent Olustur
            </button>
          </motion.div>
        ) : (
          <motion.div
            variants={stagger}
            initial="hidden"
            animate="show"
            className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
          >
            {agents.map((agent) => (
              <motion.div
                key={agent.agent_id}
                variants={fadeUp}
                onClick={() => navigate(`/agents/${agent.agent_id}`)}
                className="group relative bg-white border border-slate-200 rounded-2xl p-5 cursor-pointer hover:border-blue-300 hover:shadow-md transition-all duration-200"
              >
                {/* Delete button */}
                <button
                  onClick={(e) => handleSoftDelete(e, agent.agent_id)}
                  className="absolute top-3 right-3 p-1.5 rounded-lg text-slate-300 opacity-0 group-hover:opacity-100 hover:text-red-500 hover:bg-red-50 transition-all"
                  title="Cope tasi"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                  </svg>
                </button>

                {/* Agent icon */}
                <div className="w-10 h-10 rounded-xl bg-blue-50 flex items-center justify-center mb-3">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="4" y="4" width="16" height="16" rx="2" />
                    <path d="M9 9h.01M15 9h.01M9 15h6" />
                  </svg>
                </div>

                {/* Agent name */}
                <h3 className="text-sm font-semibold text-slate-800 truncate pr-6">
                  {agent.name}
                </h3>

                {/* Purpose */}
                {agent.purpose && (
                  <p className="text-xs text-slate-500 mt-1 line-clamp-2">{agent.purpose}</p>
                )}

                {/* Stats */}
                <div className="flex items-center gap-3 mt-3 pt-3 border-t border-slate-100">
                  {agent.is_empty ? (
                    <span className="text-xs text-slate-400">Ontoloji bos</span>
                  ) : (
                    <>
                      <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
                        {agent.entity_count} entity
                      </span>
                      <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                        {agent.relationship_count} rel
                      </span>
                    </>
                  )}
                  {agent.llm_model && (
                    <span className="ml-auto text-[10px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
                      {AVAILABLE_MODELS.find(
                        (m) => m.model === agent.llm_model,
                      )?.label || agent.llm_model}
                    </span>
                  )}
                </div>

                {/* Sohbet Baslat */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/agents/${agent.agent_id}`);
                  }}
                  className="mt-3 w-full flex items-center justify-center gap-1.5 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                  </svg>
                  Sohbet Baslat
                </button>
              </motion.div>
            ))}
          </motion.div>
        )}

        {/* Trash Section */}
        <div className="mt-8 border-t border-slate-200 pt-6">
          <button
            onClick={() => {
              setTrashOpen((v) => !v);
              if (!trashOpen) loadDeleted();
            }}
            className="flex items-center gap-2 text-sm text-slate-500 hover:text-slate-700 transition-colors"
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`transition-transform ${trashOpen ? 'rotate-90' : ''}`}
            >
              <path d="M9 18l6-6-6-6" />
            </svg>
            Cop Kutusu
            {deletedAgents.length > 0 && (
              <span className="text-xs bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded-full">
                {deletedAgents.length}
              </span>
            )}
          </button>

          <AnimatePresence>
            {trashOpen && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="mt-3 space-y-2">
                  {deletedAgents.length === 0 ? (
                    <p className="text-xs text-slate-400 pl-5">Cop kutusu bos.</p>
                  ) : (
                    deletedAgents.map((agent) => (
                      <div
                        key={agent.agent_id}
                        className="flex items-center justify-between pl-5 pr-2 py-2 rounded-lg hover:bg-slate-50 transition-colors"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="text-sm text-slate-600 truncate">{agent.name}</p>
                          <p className="text-xs text-slate-400">{formatDeletedAt(agent.deleted_at)}</p>
                        </div>
                        <div className="flex items-center gap-1 ml-2 shrink-0">
                          <button
                            onClick={() => handleRestore(agent.agent_id)}
                            className="p-1.5 text-emerald-500 hover:bg-emerald-50 rounded-lg transition-colors"
                            title="Geri yukle"
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M1 4v6h6M23 20v-6h-6" />
                              <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15" />
                            </svg>
                          </button>
                          <button
                            onClick={() => handlePurge(agent.agent_id, agent.name)}
                            className="p-1.5 text-red-400 hover:bg-red-50 rounded-lg transition-colors"
                            title="Kalici sil"
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                            </svg>
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* Create Agent Modal */}
      <AnimatePresence>
        {showCreate && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
            onClick={() => !creating && setShowCreate(false)}
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
                <h2 className="text-base font-semibold text-slate-900">Yeni Agent Olustur</h2>
                <p className="text-xs text-slate-500 mt-0.5">Yeni bir agent tanimlayip sohbet baslatin.</p>
              </div>

              <div className="px-6 py-5 space-y-4">
                <div>
                  <label className="block text-xs font-medium text-slate-600 mb-1.5">
                    Ad <span className="text-red-400">*</span>
                  </label>
                  <input
                    type="text"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="orn: Sigorta Agent"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey && newName.trim()) {
                        e.preventDefault();
                        handleCreate();
                      }
                    }}
                    className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-blue-300 focus:ring-2 focus:ring-blue-100 outline-none transition-all"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-600 mb-1.5">Amac (opsiyonel)</label>
                  <textarea
                    value={newPurpose}
                    onChange={(e) => setNewPurpose(e.target.value)}
                    placeholder="orn: Police belgelerini isle, musteri risk profili cikar"
                    rows={3}
                    className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-blue-300 focus:ring-2 focus:ring-blue-100 outline-none transition-all resize-y"
                  />
                  <p className="text-[11px] text-slate-400 mt-1">
                    Agent'in domain'i ve ilk hedefi. Sonradan degistirilebilir.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-600 mb-1.5">LLM Model</label>
                  <select
                    value={newModel}
                    onChange={(e) => setNewModel(e.target.value)}
                    className="w-full px-3 py-2 text-sm border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:border-blue-300 focus:ring-2 focus:ring-blue-100 outline-none transition-all"
                  >
                    <option value="">Sistem Varsayilani</option>
                    {AVAILABLE_MODELS.map((m) => (
                      <option key={`${m.provider}/${m.model}`} value={`${m.provider}/${m.model}`}>
                        {m.label} ({m.provider})
                      </option>
                    ))}
                  </select>
                  <p className="text-[11px] text-slate-400 mt-1">
                    Bos birakilirsa sistem varsayilani kullanilir.
                  </p>
                </div>
              </div>

              <div className="px-6 py-3 bg-slate-50 border-t border-slate-100 flex justify-end gap-2">
                <button
                  onClick={() => {
                    setShowCreate(false);
                    setNewName('');
                    setNewPurpose('');
                    setNewModel('');
                  }}
                  disabled={creating}
                  className="px-4 py-2 text-sm text-slate-600 hover:text-slate-800 transition-colors"
                >
                  Iptal
                </button>
                <button
                  onClick={handleCreate}
                  disabled={!newName.trim() || creating}
                  className="px-5 py-2 text-sm font-medium text-white bg-blue-600 rounded-xl hover:bg-blue-700 disabled:bg-blue-300 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
                >
                  {creating && (
                    <span className="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
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
