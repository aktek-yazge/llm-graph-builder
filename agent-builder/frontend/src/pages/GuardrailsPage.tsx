import { useState, useEffect, useCallback } from 'react';
import Breadcrumb from '../components/Breadcrumb';
import {
  listGuardrails, createGuardrail, updateGuardrail, deleteGuardrail,
  type Guardrail,
} from '../services/platformApi';

const GUARDRAIL_TYPES = ['content_filter', 'topic_restriction', 'output_validation', 'pii_detection'];
const TYPE_LABELS: Record<string, string> = {
  content_filter: 'Icerik Filtresi',
  topic_restriction: 'Konu Kisitlamasi',
  output_validation: 'Cikti Dogrulama',
  pii_detection: 'PII Tespiti',
};

interface FormData {
  name: string;
  guardrail_type: string;
  scope: string;
  is_active: boolean;
  config: string;
}

const emptyForm: FormData = { name: '', guardrail_type: 'content_filter', scope: 'global', is_active: true, config: '{}' };

export default function GuardrailsPage() {
  const [items, setItems] = useState<Guardrail[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormData>(emptyForm);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await listGuardrails();
      setItems(data.guardrails);
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

  function openEdit(g: Guardrail) {
    setForm({
      name: g.name,
      guardrail_type: g.guardrail_type,
      scope: g.scope,
      is_active: g.is_active,
      config: JSON.stringify(g.config, null, 2),
    });
    setEditingId(g.guardrail_id);
    setShowModal(true);
  }

  async function handleSave() {
    setSaving(true);
    let configObj: Record<string, any> = {};
    try { configObj = JSON.parse(form.config); } catch { /* keep empty */ }
    const payload: any = {
      name: form.name,
      guardrail_type: form.guardrail_type,
      scope: form.scope,
      is_active: form.is_active,
      config: configObj,
    };
    try {
      if (editingId) {
        await updateGuardrail(editingId, payload);
      } else {
        await createGuardrail(payload);
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
    if (!confirm('Bu guardrail silinecek. Emin misiniz?')) return;
    try {
      await deleteGuardrail(id);
      await load();
    } catch (e) {
      console.error('Delete failed', e);
    }
  }

  async function handleToggle(g: Guardrail) {
    try {
      await updateGuardrail(g.guardrail_id, { is_active: !g.is_active });
      await load();
    } catch (e) {
      console.error('Toggle failed', e);
    }
  }

  const q = search.toLowerCase().trim();
  const filtered = q ? items.filter((g) => g.name.toLowerCase().includes(q) || g.guardrail_type.toLowerCase().includes(q)) : items;

  return (
    <div className="max-w-6xl mx-auto p-6">
      <Breadcrumb items={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Guardrails' }]} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Guardrails</h1>
          <p className="text-sm text-gray-500 mt-1">Agent'lariniz icin guvenlik kurallari tanimlayin.</p>
        </div>
        <button onClick={openCreate} className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors">
          + Yeni Guardrail
        </button>
      </div>

      {items.length > 0 && (
        <div className="mb-4 relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Guardrail ara..."
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
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-700 mb-1">Henuz guardrail yok</h3>
          <p className="text-sm text-gray-500 mb-4">Agent'lariniz icin guvenlik kurallari tanimlayarak baslayin.</p>
          <button onClick={openCreate} className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700">+ Yeni Guardrail</button>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50/80">
                <th className="text-left px-5 py-3 text-xs font-medium text-slate-500">Guardrail</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Tur</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Kapsam</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Durum</th>
                <th className="text-right px-5 py-3 text-xs font-medium text-slate-500">Islem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.length === 0 ? (
                <tr><td colSpan={5} className="px-5 py-12 text-center text-sm text-gray-400">Sonuc bulunamadi.</td></tr>
              ) : filtered.map((g) => (
                <tr key={g.guardrail_id} className="hover:bg-blue-50/30 transition-colors">
                  <td className="px-5 py-3 font-medium text-slate-800">{g.name}</td>
                  <td className="px-3 py-3">
                    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-600">
                      {TYPE_LABELS[g.guardrail_type] || g.guardrail_type}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium ${g.scope === 'global' ? 'bg-blue-50 text-blue-600' : 'bg-purple-50 text-purple-600'}`}>
                      {g.scope}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <button onClick={() => handleToggle(g)}
                      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium cursor-pointer transition-colors ${
                        g.is_active ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                      }`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${g.is_active ? 'bg-emerald-500' : 'bg-slate-400'}`} />
                      {g.is_active ? 'Aktif' : 'Pasif'}
                    </button>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => openEdit(g)} className="px-2 py-1 text-xs text-slate-600 bg-slate-50 rounded-lg hover:bg-slate-100">Duzenle</button>
                      <button onClick={() => handleDelete(g.guardrail_id)} className="px-2 py-1 text-xs text-red-600 bg-red-50 rounded-lg hover:bg-red-100">Sil</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={() => !saving && setShowModal(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4 p-6" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold text-gray-900 mb-4">{editingId ? 'Guardrail Duzenle' : 'Yeni Guardrail'}</h2>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Ad</label>
                <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Guardrail adi" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Tur</label>
                  <select value={form.guardrail_type} onChange={(e) => setForm({ ...form, guardrail_type: e.target.value })}
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                    {GUARDRAIL_TYPES.map((t) => (<option key={t} value={t}>{TYPE_LABELS[t] || t}</option>))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Kapsam</label>
                  <select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}
                    className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                    <option value="global">Global</option>
                    <option value="agent">Agent</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                  <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                    className="rounded border-gray-300" />
                  Aktif
                </label>
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
