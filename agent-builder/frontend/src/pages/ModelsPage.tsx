import { useState, useEffect, useCallback } from 'react';
import Breadcrumb from '../components/Breadcrumb';
import { listModels, createModel, updateModel, deleteModel, type LLMModel } from '../services/platformApi';

function formatCost(v: number | null): string {
  if (v == null) return '-';
  return `$${v.toFixed(4)}`;
}

function formatNum(v: number | null): string {
  if (v == null) return '-';
  return v >= 1000 ? `${(v / 1000).toFixed(0)}K` : String(v);
}

const PROVIDERS = ['all', 'openai', 'anthropic', 'google', 'local'];
const PROVIDER_COLORS: Record<string, string> = {
  openai: 'bg-green-50 text-green-700',
  anthropic: 'bg-orange-50 text-orange-700',
  google: 'bg-blue-50 text-blue-700',
  local: 'bg-purple-50 text-purple-700',
};

interface FormData {
  provider: string;
  model_name: string;
  display_name: string;
  description: string;
  input_cost_per_1k: string;
  output_cost_per_1k: string;
  context_window: string;
  max_output_tokens: string;
  supports_vision: boolean;
  supports_function_calling: boolean;
  finetune_base_model: string;
  is_active: boolean;
}

const emptyForm: FormData = {
  provider: 'openai',
  model_name: '',
  display_name: '',
  description: '',
  input_cost_per_1k: '',
  output_cost_per_1k: '',
  context_window: '',
  max_output_tokens: '',
  supports_vision: false,
  supports_function_calling: true,
  finetune_base_model: '',
  is_active: true,
};

export default function ModelsPage() {
  const [models, setModels] = useState<LLMModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [providerFilter, setProviderFilter] = useState('all');
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormData>(emptyForm);
  const [saving, setSaving] = useState(false);

  const [showCalc, setShowCalc] = useState(false);
  const [calcModel, setCalcModel] = useState<LLMModel | null>(null);
  const [inputTokens, setInputTokens] = useState('1000');
  const [outputTokens, setOutputTokens] = useState('500');

  const load = useCallback(async () => {
    try {
      const { data } = await listModels();
      setModels(data.models);
    } catch (e) {
      console.error('Failed to load models', e);
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

  function openEdit(m: LLMModel) {
    setForm({
      provider: m.provider,
      model_name: m.model_name,
      display_name: m.display_name || '',
      description: m.description,
      input_cost_per_1k: m.input_cost_per_1k != null ? String(m.input_cost_per_1k) : '',
      output_cost_per_1k: m.output_cost_per_1k != null ? String(m.output_cost_per_1k) : '',
      context_window: m.context_window != null ? String(m.context_window) : '',
      max_output_tokens: m.max_output_tokens != null ? String(m.max_output_tokens) : '',
      supports_vision: m.supports_vision,
      supports_function_calling: m.supports_function_calling,
      finetune_base_model: m.finetune_base_model || '',
      is_active: m.is_active,
    });
    setEditingId(m.model_id);
    setShowModal(true);
  }

  async function handleSave() {
    setSaving(true);
    const payload: any = {
      provider: form.provider,
      model_name: form.model_name,
      display_name: form.display_name || null,
      description: form.description,
      input_cost_per_1k: form.input_cost_per_1k ? parseFloat(form.input_cost_per_1k) : null,
      output_cost_per_1k: form.output_cost_per_1k ? parseFloat(form.output_cost_per_1k) : null,
      context_window: form.context_window ? parseInt(form.context_window) : null,
      max_output_tokens: form.max_output_tokens ? parseInt(form.max_output_tokens) : null,
      supports_vision: form.supports_vision,
      supports_function_calling: form.supports_function_calling,
      finetune_base_model: form.finetune_base_model || null,
      is_active: form.is_active,
    };
    try {
      if (editingId) {
        await updateModel(editingId, payload);
      } else {
        await createModel(payload);
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
    if (!confirm('Bu model silinecek. Emin misiniz?')) return;
    try {
      await deleteModel(id);
      await load();
    } catch (e) {
      console.error('Delete failed', e);
    }
  }

  function openCalc(m: LLMModel) {
    setCalcModel(m);
    setShowCalc(true);
  }

  const q = search.toLowerCase().trim();
  let filtered = models;
  if (providerFilter !== 'all') {
    filtered = filtered.filter((m) => m.provider === providerFilter);
  }
  if (q) {
    filtered = filtered.filter(
      (m) =>
        m.model_name.toLowerCase().includes(q) ||
        (m.display_name || '').toLowerCase().includes(q) ||
        m.provider.toLowerCase().includes(q),
    );
  }

  const inputCost = calcModel?.input_cost_per_1k ?? 0;
  const outputCost = calcModel?.output_cost_per_1k ?? 0;
  const totalCost =
    (parseFloat(inputTokens) || 0) / 1000 * inputCost +
    (parseFloat(outputTokens) || 0) / 1000 * outputCost;

  return (
    <div className="max-w-6xl mx-auto p-6">
      <Breadcrumb items={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Models' }]} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Models</h1>
          <p className="text-sm text-gray-500 mt-1">
            LLM model bilgileri, token maliyetleri ve fine-tune ayarlari.
          </p>
        </div>
        <button onClick={openCreate} className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors">
          + Model Ekle
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex gap-1">
          {PROVIDERS.map((p) => (
            <button key={p} onClick={() => setProviderFilter(p)}
              className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                providerFilter === p ? 'bg-gray-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}>
              {p === 'all' ? 'Tumu' : p.charAt(0).toUpperCase() + p.slice(1)}
            </button>
          ))}
        </div>
        <div className="flex-1 relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Model ara..."
            className="w-full pl-10 pr-4 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-blue-200 border-t-blue-600" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl border border-gray-200">
          <div className="w-14 h-14 mx-auto mb-3 rounded-full bg-slate-100 flex items-center justify-center">
            <svg className="w-7 h-7 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8.25 3v1.5M4.5 8.25H3m18 0h-1.5M4.5 12H3m18 0h-1.5m-15 3.75H3m18 0h-1.5M8.25 19.5V21M12 3v1.5m0 15V21m3.75-18v1.5m0 15V21m-9-1.5h10.5a2.25 2.25 0 002.25-2.25V6.75a2.25 2.25 0 00-2.25-2.25H6.75A2.25 2.25 0 004.5 6.75v10.5a2.25 2.25 0 002.25 2.25z" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-700 mb-1">{q || providerFilter !== 'all' ? 'Sonuc bulunamadi' : 'Henuz model yok'}</h3>
          <p className="text-sm text-gray-500 mb-4">LLM modellerini ekleyerek maliyet ve kapasite bilgilerini yonetin.</p>
          {!q && providerFilter === 'all' && (
            <button onClick={openCreate} className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700">+ Model Ekle</button>
          )}
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50/80">
                <th className="text-left px-5 py-3 text-xs font-medium text-slate-500">Model</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Provider</th>
                <th className="text-right px-3 py-3 text-xs font-medium text-slate-500">Input ($/1K)</th>
                <th className="text-right px-3 py-3 text-xs font-medium text-slate-500">Output ($/1K)</th>
                <th className="text-right px-3 py-3 text-xs font-medium text-slate-500">Context</th>
                <th className="text-center px-3 py-3 text-xs font-medium text-slate-500">Vision</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500 hidden lg:table-cell">Fine-tune</th>
                <th className="text-right px-5 py-3 text-xs font-medium text-slate-500">Islem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.map((m) => (
                <tr key={m.model_id} className="hover:bg-blue-50/30 transition-colors">
                  <td className="px-5 py-3">
                    <span className="font-medium text-slate-800">{m.display_name || m.model_name}</span>
                    {m.display_name && <span className="text-[10px] text-slate-400 ml-1.5">{m.model_name}</span>}
                  </td>
                  <td className="px-3 py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium ${PROVIDER_COLORS[m.provider] || 'bg-slate-100 text-slate-600'}`}>
                      {m.provider}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-right text-slate-600 tabular-nums">{formatCost(m.input_cost_per_1k)}</td>
                  <td className="px-3 py-3 text-right text-slate-600 tabular-nums">{formatCost(m.output_cost_per_1k)}</td>
                  <td className="px-3 py-3 text-right text-slate-600 tabular-nums">{formatNum(m.context_window)}</td>
                  <td className="px-3 py-3 text-center">
                    {m.supports_vision ? (
                      <span className="inline-block w-2 h-2 rounded-full bg-emerald-500" title="Vision destekli" />
                    ) : (
                      <span className="inline-block w-2 h-2 rounded-full bg-slate-300" />
                    )}
                  </td>
                  <td className="px-3 py-3 hidden lg:table-cell">
                    {m.finetune_status ? (
                      <span className={`text-xs font-medium ${m.finetune_status === 'ready' ? 'text-emerald-600' : 'text-amber-600'}`}>{m.finetune_status}</span>
                    ) : (
                      <span className="text-xs text-slate-400">-</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => openCalc(m)} className="px-2 py-1 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100" title="Maliyet Hesapla">$</button>
                      <button onClick={() => openEdit(m)} className="px-2 py-1 text-xs text-slate-600 bg-slate-50 rounded-lg hover:bg-slate-100">Duzenle</button>
                      <button onClick={() => handleDelete(m.model_id)} className="px-2 py-1 text-xs text-red-600 bg-red-50 rounded-lg hover:bg-red-100">Sil</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={() => !saving && setShowModal(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg mx-4 p-6 max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold text-gray-900 mb-4">{editingId ? 'Modeli Duzenle' : 'Yeni Model Ekle'}</h2>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Provider</label>
                <select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="google">Google</option>
                  <option value="local">Local</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Model Name</label>
                <input value={form.model_name} onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="gpt-4o" />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-600 mb-1">Display Name</label>
                <input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="GPT-4o" />
              </div>
              <div className="col-span-2">
                <label className="block text-xs font-medium text-gray-600 mb-1">Aciklama</label>
                <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 h-16 resize-none" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Input Cost ($/1K token)</label>
                <input type="number" step="0.0001" value={form.input_cost_per_1k} onChange={(e) => setForm({ ...form, input_cost_per_1k: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Output Cost ($/1K token)</label>
                <input type="number" step="0.0001" value={form.output_cost_per_1k} onChange={(e) => setForm({ ...form, output_cost_per_1k: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Context Window</label>
                <input type="number" value={form.context_window} onChange={(e) => setForm({ ...form, context_window: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="128000" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Max Output Tokens</label>
                <input type="number" value={form.max_output_tokens} onChange={(e) => setForm({ ...form, max_output_tokens: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="4096" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Fine-tune Base Model</label>
                <input value={form.finetune_base_model} onChange={(e) => setForm({ ...form, finetune_base_model: e.target.value })}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Bos birakin desteklemiyorsa" />
              </div>
              <div className="flex items-end pb-1 gap-4">
                <label className="flex items-center gap-1.5 text-sm text-gray-700 cursor-pointer">
                  <input type="checkbox" checked={form.supports_vision} onChange={(e) => setForm({ ...form, supports_vision: e.target.checked })}
                    className="rounded border-gray-300" />
                  Vision
                </label>
                <label className="flex items-center gap-1.5 text-sm text-gray-700 cursor-pointer">
                  <input type="checkbox" checked={form.supports_function_calling} onChange={(e) => setForm({ ...form, supports_function_calling: e.target.checked })}
                    className="rounded border-gray-300" />
                  Functions
                </label>
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setShowModal(false)} disabled={saving}
                className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50">Iptal</button>
              <button onClick={handleSave} disabled={saving || !form.model_name || !form.provider}
                className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50">
                {saving ? 'Kaydediliyor...' : editingId ? 'Guncelle' : 'Olustur'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Cost Calculator Modal */}
      {showCalc && calcModel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={() => setShowCalc(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm mx-4 p-6" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold text-gray-900 mb-1">Maliyet Hesaplayici</h2>
            <p className="text-sm text-gray-500 mb-4">{calcModel.display_name || calcModel.model_name}</p>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Input Token</label>
                <input type="number" value={inputTokens} onChange={(e) => setInputTokens(e.target.value)}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Output Token</label>
                <input type="number" value={outputTokens} onChange={(e) => setOutputTokens(e.target.value)}
                  className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>

              <div className="bg-slate-50 rounded-xl p-4 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-gray-500">Input maliyeti:</span>
                  <span className="font-medium text-gray-800">${((parseFloat(inputTokens) || 0) / 1000 * inputCost).toFixed(6)}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-500">Output maliyeti:</span>
                  <span className="font-medium text-gray-800">${((parseFloat(outputTokens) || 0) / 1000 * outputCost).toFixed(6)}</span>
                </div>
                <div className="border-t border-slate-200 pt-2 flex justify-between text-sm">
                  <span className="font-medium text-gray-700">Toplam:</span>
                  <span className="font-bold text-blue-600">${totalCost.toFixed(6)}</span>
                </div>
              </div>
            </div>

            <div className="flex justify-end mt-4">
              <button onClick={() => setShowCalc(false)} className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200">Kapat</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
