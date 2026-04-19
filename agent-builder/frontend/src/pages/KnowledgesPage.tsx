import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import Breadcrumb from '../components/Breadcrumb';
import {
  listKnowledgeBases,
  type KnowledgeBase,
} from '../services/platformApi';

function formatDate(iso: string | null): string {
  if (!iso) return '-';
  return new Date(iso).toLocaleDateString('tr-TR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-emerald-50 text-emerald-700',
  inactive: 'bg-slate-100 text-slate-500',
  archived: 'bg-amber-50 text-amber-700',
};

export default function KnowledgesPage() {
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await listKnowledgeBases();
      setItems(data.knowledge_bases);
    } catch (e) {
      console.error('Failed to load knowledge bases', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const q = search.toLowerCase().trim();
  const filtered = q
    ? items.filter(
        (kb) =>
          (kb.name || '').toLowerCase().includes(q) ||
          kb.agent_name.toLowerCase().includes(q) ||
          kb.workflow_name.toLowerCase().includes(q),
      )
    : items;

  const totalEntities = items.reduce((s, kb) => s + kb.entity_type_count, 0);
  const totalRelations = items.reduce((s, kb) => s + kb.relationship_type_count, 0);
  const totalDocs = items.reduce((s, kb) => s + kb.source_document_count, 0);

  return (
    <div className="max-w-6xl mx-auto p-6">
      <Breadcrumb items={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Knowledges' }]} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Knowledge Bases</h1>
          <p className="text-sm text-gray-500 mt-1">
            Workflow'lar tarafindan olusturulan bilgi tabanlari.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
        >
          {loading ? 'Yukleniyor...' : 'Yenile'}
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {[
          { label: 'Knowledge Base', value: items.length, color: 'text-blue-700' },
          { label: 'Kaynak Belge', value: totalDocs, color: 'text-violet-700' },
          { label: 'Entity Tipi', value: totalEntities, color: 'text-emerald-700' },
          { label: 'Iliski Tipi', value: totalRelations, color: 'text-amber-700' },
        ].map((s) => (
          <div key={s.label} className="bg-white rounded-xl border border-gray-200 p-4">
            <p className="text-xs text-gray-500">{s.label}</p>
            <p className={`text-2xl font-bold mt-1 ${s.color}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {items.length > 0 && (
        <div className="mb-4 relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="KB, agent veya workflow ara..."
            className="w-full pl-10 pr-4 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
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
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-700 mb-1">Henuz Knowledge Base yok</h3>
          <p className="text-sm text-gray-500 mb-4">
            Bir agent'in workflow'unu calistirip publish ettiginizde burada gorunecek.
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50/80">
                <th className="text-left px-5 py-3 text-xs font-medium text-slate-500">Knowledge Base</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Agent</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500 hidden lg:table-cell">Workflow</th>
                <th className="text-right px-3 py-3 text-xs font-medium text-slate-500">Kaynaklar</th>
                <th className="text-right px-3 py-3 text-xs font-medium text-slate-500 hidden md:table-cell">Entity</th>
                <th className="text-right px-3 py-3 text-xs font-medium text-slate-500 hidden md:table-cell">Iliski</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Durum</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500 hidden xl:table-cell">Tarih</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-5 py-12 text-center text-sm text-gray-400">
                    Sonuc bulunamadi.
                  </td>
                </tr>
              ) : (
                filtered.map((kb) => (
                  <tr
                    key={kb.endpoint_id}
                    className="hover:bg-blue-50/30 transition-colors cursor-pointer"
                    onClick={() => navigate(`/knowledges/${kb.endpoint_id}`)}
                  >
                    <td className="px-5 py-3">
                      <div className="font-medium text-slate-800">
                        {kb.name || kb.agent_name || kb.endpoint_id}
                      </div>
                      <div className="text-[10px] text-slate-400 font-mono mt-0.5">
                        {kb.endpoint_id}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <span className="text-xs text-blue-600">{kb.agent_name}</span>
                    </td>
                    <td className="px-3 py-3 hidden lg:table-cell">
                      <span className="text-xs text-slate-500">
                        {kb.workflow_name || '-'}
                        {kb.workflow_version > 1 && (
                          <span className="ml-1 text-slate-400">v{kb.workflow_version}</span>
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-slate-600">
                      {kb.source_document_count}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-slate-600 hidden md:table-cell">
                      {kb.entity_type_count}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-slate-600 hidden md:table-cell">
                      {kb.relationship_type_count}
                    </td>
                    <td className="px-3 py-3">
                      <span
                        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium ${
                          STATUS_STYLES[kb.status] || 'bg-slate-100 text-slate-600'
                        }`}
                      >
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            kb.status === 'active' ? 'bg-emerald-500' : 'bg-slate-400'
                          }`}
                        />
                        {kb.status === 'active' ? 'Aktif' : kb.status}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-400 hidden xl:table-cell">
                      {formatDate(kb.published_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
