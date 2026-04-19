import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import Breadcrumb from '../components/Breadcrumb';
import {
  listGlobalResources,
  type GlobalResourceItem,
} from '../services/evolvingApi';

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-';
  return new Date(iso).toLocaleDateString('tr-TR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatSize(bytes: number): string {
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ResourcesPage() {
  const [resources, setResources] = useState<GlobalResourceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await listGlobalResources();
      setResources(data.resources);
    } catch (e) {
      console.error('Failed to load resources', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const q = search.toLowerCase().trim();
  const filtered = q
    ? resources.filter(
        (r) =>
          r.filename.toLowerCase().includes(q) ||
          r.agent_name.toLowerCase().includes(q) ||
          r.content_type.toLowerCase().includes(q),
      )
    : resources;

  const agents = new Set(resources.map((r) => r.agent_id));
  const totalSize = resources.reduce((s, r) => s + (r.size || 0), 0);

  return (
    <div className="max-w-6xl mx-auto p-6">
      <Breadcrumb items={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Kaynaklar' }]} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Kaynaklar</h1>
          <p className="text-sm text-gray-500 mt-1">
            Tum agentlara ait belgeler ve dosyalar
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
      <div className="grid grid-cols-3 gap-4 mb-6">
        {[
          { label: 'Toplam Kaynak', value: resources.length, color: 'text-blue-700' },
          { label: 'Agent', value: agents.size, color: 'text-violet-700' },
          { label: 'Toplam Boyut', value: formatSize(totalSize), color: 'text-emerald-700' },
        ].map((s) => (
          <div key={s.label} className="bg-white rounded-xl border border-gray-200 p-4">
            <p className="text-xs text-gray-500">{s.label}</p>
            <p className={`text-2xl font-bold mt-1 ${s.color}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {resources.length > 0 && (
        <div className="mb-4 relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Dosya adi, agent veya tur ile ara..."
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
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-700 mb-1">Henuz kaynak yok</h3>
          <p className="text-sm text-gray-500">
            Agent'lara dosya yukleyerek baslayabilirsiniz.
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50/80">
                <th className="text-left px-5 py-3 text-xs font-medium text-slate-500">Dosya</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Agent</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Tur</th>
                <th className="text-right px-3 py-3 text-xs font-medium text-slate-500">Boyut</th>
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500 hidden lg:table-cell">Tarih</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-5 py-12 text-center text-sm text-gray-400">
                    Sonuc bulunamadi.
                  </td>
                </tr>
              ) : (
                filtered.map((r, i) => (
                  <tr
                    key={`${r.resource_id}-${i}`}
                    className="hover:bg-blue-50/30 transition-colors"
                  >
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center shrink-0">
                          <FileIcon contentType={r.content_type} />
                        </div>
                        <div className="min-w-0">
                          <div className="font-medium text-slate-800 truncate max-w-[280px]">
                            {r.filename}
                          </div>
                          <div className="text-[10px] text-slate-400 font-mono mt-0.5 truncate max-w-[280px]">
                            {r.content_type || r.type}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <span
                        className="text-xs text-blue-600 hover:underline cursor-pointer"
                        onClick={() => navigate(`/agents/${r.agent_id}`)}
                      >
                        {r.agent_name}
                      </span>
                    </td>
                    <td className="px-3 py-3">
                      <span className="inline-flex px-2 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-600">
                        {r.type === 'file' ? 'Dosya' : r.type}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right text-slate-600 tabular-nums text-xs">
                      {formatSize(r.size)}
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-400 hidden lg:table-cell">
                      {formatDate(r.created_at)}
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

function FileIcon({ contentType }: { contentType: string }) {
  const ct = (contentType || '').toLowerCase();
  if (ct.includes('pdf')) {
    return (
      <svg className="w-4 h-4 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    );
  }
  if (ct.includes('image')) {
    return (
      <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    );
  }
  if (ct.includes('spreadsheet') || ct.includes('excel') || ct.includes('csv')) {
    return (
      <svg className="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
      </svg>
    );
  }
  return (
    <svg className="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  );
}
