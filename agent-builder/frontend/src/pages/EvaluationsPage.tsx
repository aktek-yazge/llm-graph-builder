import { useState, useEffect, useCallback } from 'react';
import Breadcrumb from '../components/Breadcrumb';
import {
  listEvaluations, createEvaluation, updateEvaluation, deleteEvaluation,
  type Evaluation,
} from '../services/platformApi';
import { listAgents, type AgentInfo } from '../services/evolvingApi';

const EVAL_TYPES = ['accuracy', 'relevance', 'faithfulness', 'custom'];
const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-slate-100 text-slate-600',
  running: 'bg-amber-50 text-amber-700',
  completed: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-600',
};

interface FormData {
  name: string;
  agent_id: string;
  eval_type: string;
  status: string;
  config: string;
}

const emptyForm: FormData = { name: '', agent_id: '', eval_type: 'accuracy', status: 'draft', config: '{}' };

export default function EvaluationsPage() {
  const [items, setItems] = useState<Evaluation[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormData>(emptyForm);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [{ data: evalData }, { data: agentData }] = await Promise.all([
        listEvaluations(),
        listAgents(200),
      ]);
      setItems(evalData.evaluations);
      setAgents(agentData);
    } catch (e) {
      console.error('Load failed', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function openCreate() {
    setForm(emptyForm);
    setEditingId(null);
    setShowModal(true);
  }

  function openEdit(ev: Evaluation) {
    setForm({
      name: ev.name,
      agent_id: ev.agent_id || '',
      eval_type: ev.eval_type,
      status: ev.status,
      config: JSON.stringify(ev.config, null, 2),
    });
    setEditingId(ev.evaluation_id);
    setShowModal(true);
  }

  async function handleSave() {
    setSaving(true);
    let configObj: Record<string, any> = {};
    try { configObj = JSON.parse(form.config); } catch { /* keep empty */ }
    const payload: any = {
      name: form.name,
      agent_id: form.agent_id || null,
      eval_type: form.eval_type,
      status: form.status,
      config: configObj,
    };
    try {
      if (editingId) {
        await updateEvaluation(editingId, payload);
      } else {
        await createEvaluation(payload);
      }
      setShowModal(false);
      await load();
    } catch (e) {
      console.error('Save failed', e);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('Bu evaluation silinecek. Emin misiniz?')) return;
    try {
      await deleteEvaluation(id);
      await load();
    } catch (e) {
      console.error('Delete failed', e);
    }
  }

  const q = search.toLowerCase().trim();
  const filtered = q ? items.filter((ev) => ev.name.toLowerCase().includes(q)) : items;

  return (
    <div className="max-w-6xl mx-auto p-6">
      <Breadcrumb items={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Evaluations' }]} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Evaluations</h1>
          <p className="text-sm text-gray-500 mt-1">Agent performansini olcun, test case'leri tanimlayip sonuclari karsilastirin.</p>
        </div>
        <button onClick={openCreate} className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors">
          + Yeni Evaluation
        </button>
      </div>

      {items.length > 0 && (
        <div className="mb-4 relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Evaluation ara..."
            className="w-full pl-10 pr-4 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-blue-200 border-t-blue-600" />
        </div>
      ) : filtered.length === 0 && !q ? (
        <div className="text-center py-16 bg-white rounded-xl border border-gray-200">
          <div className="w-14 h-14 mx-auto mb-3 rounded-full bg-slate-100 flex items-center justify-center">
            <svg className="w-7 h-7 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-700 mb-1">Henuz evaluation yok</h3>
          <p className="text-sm text-gray-500 mb-4">Agent'larinizi test etmek icin bir evaluation tanimlayarak baslayin.</p>
          <button onClick={openCreate} className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700">+ Yeni Evaluation</button>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50/80">
                <th className="text-left px-5 py-3 text-xs font-medium text-slate-500">Evaluation</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Agent</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Tur</th>
                <th className="text-right px-3 py-3 text-xs font-medium text-slate-500">Skor</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Durum</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500 hidden lg:table-cell">Son Calistirma</th>
                <th className="text-right px-5 py-3 text-xs font-medium text-slate-500">Islem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.length === 0 ? (
                <tr><td colSpan={7} className="px-5 py-12 text-center text-sm text-gray-400">Sonuc bulunamadi.</td></tr>
              ) : filtered.map((ev) => {
                const agentName = agents.find((a) => a.agent_id === ev.agent_id)?.name;
                return (
                  <tr key={ev.evaluation_id} className="hover:bg-blue-50/30 transition-colors">
                    <td className="px-5 py-3 font-medium text-slate-800">{ev.name}</td>
                    <td className="px-3 py-3 text-xs text-slate-500">{agentName || ev.agent_id || '-'}</td>
                    <td className="px-3 py-3">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-600">{ev.eval_type}</span>
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{ev.score != null ? ev.score.toFixed(1) : '-'}</td>
                    <td className="px-3 py-3">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium ${STATUS_COLORS[ev.status] || 'bg-slate-100 text-slate-600'}`}>{ev.status}</span>
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-400 hidden lg:table-cell">{ev.last_run_at ? new Date(ev.last_run_at).toLocaleString('tr-TR') : '-'}</td>
                    <td className="px-5 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button onClick={() => openEdit(ev)} className="px-2 py-1 text-xs text-slate-600 bg-slate-50 rounded-lg hover:bg-slate-100">Duzenle</button>
                        <button onClick={() => handleDelete(ev.evaluation_id)} className="px-2 py-1 text-xs text-red-600 bg-red-50 rounded-lg hover:bg-red-100">Sil</button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={() => !saving && setShowModal(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold text-gray-900 mb-4">{editingId ? 'Evaluation Duzenle' : 'Yeni Evaluation'}</h2>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Ad</label>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Evaluation adi" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Agent (opsiyonel)</label>
                <select value={form.agent_id} onChange={(e) => setForm({ ...form, agent_id: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="">Genel</option>
                  {agents.map((a) => (<option key={a.agent_id} value={a.agent_id}>{a.name}</option>))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Tur</label>
                  <select value={form.eval_type} onChange={(e) => setForm({ ...form, eval_type: e.target.value })}
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                    {EVAL_TYPES.map((t) => (<option key={t} value={t}>{t}</option>))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Durum</label>
                  <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                    <option value="draft">Draft</option>
                    <option value="running">Running</option>
                    <option value="completed">Completed</option>
                    <option value="failed">Failed</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Config (JSON)</label>
                <textarea value={form.config} onChange={(e) => setForm({ ...form, config: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 h-24 resize-none font-mono" />
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setShowModal(false)} disabled={saving}
                className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50">Iptal</button>
              <button onClick={handleSave} disabled={saving || !form.name}
                className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50">
                {saving ? 'Kaydediliyor...' : editingId ? 'Guncelle' : 'Olustur'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
