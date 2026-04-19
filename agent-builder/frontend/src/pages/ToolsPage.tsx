import { useState, useEffect, useCallback } from 'react';
import Breadcrumb from '../components/Breadcrumb';
import {
  listMcpTools, toggleMcpTool,
  type McpTool,
} from '../services/platformApi';

function formatToolName(tool: McpTool): string {
  return tool.displayName || tool.customName || tool.originalName || tool.name;
}

function categorize(name: string): string {
  if (name.startsWith('llm-graph-builder-extract-') || name.startsWith('extract_')) return 'Extraction';
  if (name.startsWith('llm-graph-builder-storage-') || name.startsWith('storage_')) return 'Storage';
  if (name.startsWith('llm-graph-builder-neo4j-') || name.startsWith('neo4j_')) return 'Neo4j';
  return 'Diger';
}

const CATEGORY_COLORS: Record<string, string> = {
  Extraction: 'bg-violet-50 text-violet-700',
  Storage: 'bg-blue-50 text-blue-700',
  Neo4j: 'bg-emerald-50 text-emerald-700',
  Diger: 'bg-slate-100 text-slate-600',
};

export default function ToolsPage() {
  const [allTools, setAllTools] = useState<McpTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [selectedTool, setSelectedTool] = useState<McpTool | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await listMcpTools();
      const mcpTools = data.tools.filter(
        (t) => t.gatewayId && t.gatewaySlug,
      );
      setAllTools(mcpTools);
    } catch (e: any) {
      const msg = e.response?.data?.detail || e.message || 'MCP Gateway baglantisi basarisiz';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function handleToggle(tool: McpTool) {
    try {
      await toggleMcpTool(tool.id, !tool.enabled);
      setAllTools((prev) =>
        prev.map((t) => (t.id === tool.id ? { ...t, enabled: !t.enabled } : t)),
      );
      if (selectedTool?.id === tool.id) {
        setSelectedTool({ ...tool, enabled: !tool.enabled });
      }
    } catch (e) {
      console.error('Toggle failed', e);
    }
  }

  const q = search.toLowerCase().trim();
  const filtered = q
    ? allTools.filter(
        (t) =>
          formatToolName(t).toLowerCase().includes(q) ||
          t.description.toLowerCase().includes(q) ||
          t.originalName.toLowerCase().includes(q),
      )
    : allTools;

  const grouped: Record<string, McpTool[]> = {};
  for (const t of filtered) {
    const cat = categorize(t.name || t.originalName);
    if (!grouped[cat]) grouped[cat] = [];
    grouped[cat].push(t);
  }
  const categoryOrder = ['Extraction', 'Storage', 'Neo4j', 'Diger'];
  const sortedCategories = categoryOrder.filter((c) => grouped[c]);

  const enabledCount = allTools.filter((t) => t.enabled).length;
  const reachableCount = allTools.filter((t) => t.reachable).length;

  return (
    <div className="max-w-6xl mx-auto p-6">
      <Breadcrumb items={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Tools' }]} />

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">MCP Tools</h1>
          <p className="text-sm text-gray-500 mt-1">
            MCP Gateway uzerinden kullanilabilir tool'lar.
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
          { label: 'Toplam Tool', value: allTools.length, color: 'text-blue-700' },
          { label: 'Aktif', value: enabledCount, color: 'text-emerald-700' },
          { label: 'Erisilebilir', value: reachableCount, color: 'text-green-700' },
        ].map((s) => (
          <div key={s.label} className="bg-white rounded-xl border border-gray-200 p-4">
            <p className="text-xs text-gray-500">{s.label}</p>
            <p className={`text-2xl font-bold mt-1 ${s.color}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
          <strong>Hata:</strong> {error}
        </div>
      )}

      {allTools.length > 0 && (
        <div className="mb-4 relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tool ara..."
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
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M11.42 15.17l-5.384-3.108A1.5 1.5 0 005.273 14.1l5.384 3.108a1.5 1.5 0 001.686 0l5.384-3.108a1.5 1.5 0 00-.763-2.037l-5.384-3.108a1.5 1.5 0 00-1.686 0z" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-700 mb-1">MCP Gateway'de tool yok</h3>
          <p className="text-sm text-gray-500 mb-4">Gateway'e MCP sunucusu kaydedip tool'larin kesfedilmesini saglayin.</p>
        </div>
      ) : (
        sortedCategories.map((cat) => (
          <div key={cat} className="mb-6">
            <div className="flex items-center gap-2 mb-2">
              <span className={`inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium ${CATEGORY_COLORS[cat]}`}>
                {cat}
              </span>
              <span className="text-xs text-gray-400">{grouped[cat].length} tool</span>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-slate-50/80">
                    <th className="text-left px-5 py-3 text-xs font-medium text-slate-500">Tool</th>
                    <th className="text-left px-3 py-3 text-xs font-medium text-slate-500 hidden lg:table-cell">Aciklama</th>
                    <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Durum</th>
                    <th className="text-right px-5 py-3 text-xs font-medium text-slate-500">Islem</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {grouped[cat].map((t) => (
                    <tr key={t.id} className="hover:bg-blue-50/30 transition-colors cursor-pointer" onClick={() => setSelectedTool(t)}>
                      <td className="px-5 py-3">
                        <div className="font-medium text-slate-800">{formatToolName(t)}</div>
                        <div className="text-xs text-slate-400 mt-0.5 font-mono">{t.originalName}</div>
                      </td>
                      <td className="px-3 py-3 text-xs text-slate-500 hidden lg:table-cell">
                        <span className="truncate block max-w-[300px]">{t.description ? t.description.slice(0, 120) : '-'}</span>
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={(e) => { e.stopPropagation(); handleToggle(t); }}
                            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium cursor-pointer transition-colors ${
                              t.enabled
                                ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                                : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                            }`}
                          >
                            <span className={`w-1.5 h-1.5 rounded-full ${t.enabled ? 'bg-emerald-500' : 'bg-slate-400'}`} />
                            {t.enabled ? 'Aktif' : 'Pasif'}
                          </button>
                          {t.reachable ? (
                            <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                          ) : (
                            <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
                          )}
                        </div>
                      </td>
                      <td className="px-5 py-3 text-right">
                        <button
                          onClick={(e) => { e.stopPropagation(); setSelectedTool(t); }}
                          className="px-2 py-1 text-xs text-slate-600 bg-slate-50 rounded-lg hover:bg-slate-100"
                        >
                          Detay
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}

      {/* Tool Detail Modal */}
      {selectedTool && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={() => setSelectedTool(null)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-4xl mx-4 max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between rounded-t-2xl">
              <h2 className="text-lg font-semibold text-gray-900">Tool Detayi</h2>
              <button onClick={() => setSelectedTool(null)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
            </div>
            <div className="p-6 space-y-5">
              <div>
                <h3 className="text-xl font-bold text-gray-900">{formatToolName(selectedTool)}</h3>
                <p className="text-xs text-gray-400 font-mono mt-1">{selectedTool.originalName}</p>
              </div>

              <div className="flex items-center gap-2 flex-wrap">
                <button
                  onClick={() => handleToggle(selectedTool)}
                  className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-sm font-medium cursor-pointer transition-colors ${
                    selectedTool.enabled
                      ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                      : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                  }`}
                >
                  <span className={`w-2 h-2 rounded-full ${selectedTool.enabled ? 'bg-emerald-500' : 'bg-slate-400'}`} />
                  {selectedTool.enabled ? 'Aktif' : 'Pasif'}
                </button>
                <span className={`inline-flex items-center gap-1 px-3 py-1 rounded-lg text-sm font-medium ${
                  selectedTool.reachable ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'
                }`}>
                  <span className={`w-2 h-2 rounded-full ${selectedTool.reachable ? 'bg-green-500' : 'bg-red-400'}`} />
                  {selectedTool.reachable ? 'Erisilebilir' : 'Erisilemez'}
                </span>
                <span className="px-3 py-1 rounded-lg text-sm bg-blue-50 text-blue-600">{selectedTool.requestType}</span>
              </div>

              {selectedTool.description && (
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Aciklama</label>
                  <p className="text-sm text-gray-700">{selectedTool.description}</p>
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Kategori</label>
                  <p className="text-sm text-gray-800">{categorize(selectedTool.name || selectedTool.originalName)}</p>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Versiyon</label>
                  <p className="text-sm text-gray-800">{selectedTool.version || '-'}</p>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Gateway</label>
                  <p className="text-sm text-gray-800">{selectedTool.gatewaySlug || '-'}</p>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Olusturulma</label>
                  <p className="text-sm text-gray-800">{selectedTool.createdAt ? new Date(selectedTool.createdAt).toLocaleString('tr-TR') : '-'}</p>
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">URL</label>
                <code className="block text-xs text-gray-600 bg-gray-50 rounded-lg p-2 break-all">{selectedTool.url}</code>
              </div>

              {selectedTool.inputSchema && Object.keys(selectedTool.inputSchema).length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Input Schema</label>
                  <pre className="text-xs bg-slate-50 rounded-lg p-3 overflow-x-auto max-h-64 border border-slate-200">
                    {JSON.stringify(selectedTool.inputSchema, null, 2)}
                  </pre>
                </div>
              )}

              {selectedTool.outputSchema && Object.keys(selectedTool.outputSchema).length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">Output Schema</label>
                  <pre className="text-xs bg-slate-50 rounded-lg p-3 overflow-x-auto max-h-64 border border-slate-200">
                    {JSON.stringify(selectedTool.outputSchema, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
