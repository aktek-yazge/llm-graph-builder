import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listGlobalResources,
  uploadGlobalResource,
  deleteGlobalResource,
  listAgents,
  GlobalResourceItem,
  AgentInfo,
} from '../services/evolvingApi';
import Breadcrumb from '../components/Breadcrumb';

const ACCEPTED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.tif', '.txt', '.md'];

function filterAcceptedFiles(files: File[]): File[] {
  return files.filter((f) => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
    return ACCEPTED_EXTENSIONS.includes(ext);
  });
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function fileIcon(contentType: string) {
  if (contentType?.startsWith('image/')) return '🖼️';
  if (contentType?.includes('pdf')) return '📄';
  return '📎';
}

export default function ResourceList() {
  const [resources, setResources] = useState<GlobalResourceItem[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const [showUpload, setShowUpload] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const loadResources = useCallback(async () => {
    try {
      const { data } = await listGlobalResources();
      setResources(data.resources);
    } catch (e) {
      console.error('Failed to load global resources', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAgents = useCallback(async () => {
    try {
      const { data } = await listAgents(200);
      setAgents(data);
    } catch (e) {
      console.error('Failed to load agents', e);
    }
  }, []);

  useEffect(() => {
    loadResources();
    loadAgents();
  }, [loadResources, loadAgents]);

  async function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const rawFiles = e.target.files;
    if (!rawFiles?.length || !selectedAgentId) return;

    const files = filterAcceptedFiles(Array.from(rawFiles));
    if (!files.length) {
      alert('Secilen dosyalar arasinda desteklenen format bulunamadi.');
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }

    setUploading(true);
    try {
      await uploadGlobalResource(selectedAgentId, files);
      setShowUpload(false);
      setSelectedAgentId('');
      await loadResources();
    } catch (err) {
      console.error('Upload failed', err);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  async function handleDelete(resourceId: string) {
    if (!confirm('Bu kaynak silinecek. Emin misiniz?')) return;
    try {
      await deleteGlobalResource(resourceId);
      await loadResources();
    } catch (e) {
      console.error('Delete failed', e);
    }
  }

  const q = search.toLowerCase().trim();
  const filtered = q
    ? resources.filter(
        (r) =>
          r.filename.toLowerCase().includes(q) ||
          r.agent_name.toLowerCase().includes(q) ||
          r.type.toLowerCase().includes(q),
      )
    : resources;

  const agentGroups = new Map<string, { name: string; count: number }>();
  for (const r of resources) {
    const g = agentGroups.get(r.agent_id);
    if (g) g.count++;
    else agentGroups.set(r.agent_id, { name: r.agent_name, count: 1 });
  }

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <div className="flex items-center justify-center py-20">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-blue-200 border-t-blue-600" />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto p-6">
      <input
        type="file"
        ref={fileInputRef}
        className="hidden"
        multiple
        accept=".pdf,.png,.jpg,.jpeg,.tiff,.tif,.txt,.md"
        onChange={handleFileSelect}
      />

      <Breadcrumb
        items={[
          { label: 'Dashboard', to: '/dashboard' },
          { label: 'Resources' },
        ]}
      />

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Resources</h1>
          <p className="text-sm text-gray-500 mt-1">
            Tum agent'lara ait kaynaklari global olarak goruntuleyin ve yonetin.
          </p>
        </div>
        <button
          onClick={() => setShowUpload(true)}
          className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors"
        >
          + Kaynak Yukle
        </button>
      </div>

      {/* Stats */}
      {resources.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-blue-50 text-blue-700 border border-blue-100">
            Toplam: {resources.length} kaynak
          </span>
          {Array.from(agentGroups.entries()).map(([agentId, info]) => (
            <button
              key={agentId}
              onClick={() => setSearch(info.name)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-50 text-slate-600 border border-slate-200 hover:bg-slate-100 transition-colors"
            >
              {info.name}: {info.count}
            </button>
          ))}
        </div>
      )}

      {/* Search */}
      {resources.length > 0 && (
        <div className="mb-4 relative">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Kaynak ara... (dosya adi, agent, tur)"
            className="w-full pl-10 pr-8 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      )}

      {/* Resource Table */}
      {resources.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl border border-gray-200">
          <div className="w-14 h-14 mx-auto mb-3 rounded-full bg-slate-100 flex items-center justify-center">
            <svg className="w-7 h-7 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
              />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-700 mb-1">Henuz kaynak yok</h3>
          <p className="text-sm text-gray-500 mb-4">
            Agent'lariniza dosya yukleyerek baslayin veya asagidaki butonla kaynak ekleyin.
          </p>
          <button
            onClick={() => setShowUpload(true)}
            className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700"
          >
            + Kaynak Yukle
          </button>
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
                <th className="text-left px-3 py-3 text-xs font-medium text-slate-500 hidden lg:table-cell">
                  Tarih
                </th>
                <th className="text-right px-5 py-3 text-xs font-medium text-slate-500">Islem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.length === 0 && q ? (
                <tr>
                  <td colSpan={6} className="px-5 py-12 text-center text-sm text-gray-400">
                    "<span className="font-medium text-gray-600">{search}</span>" ile eslesen kaynak
                    bulunamadi.
                  </td>
                </tr>
              ) : (
                filtered.map((r) => (
                  <tr
                    key={r.resource_id}
                    className="hover:bg-blue-50/30 transition-colors"
                  >
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <span className="text-base">{fileIcon(r.content_type)}</span>
                        <div className="min-w-0">
                          <span className="font-medium text-slate-800 block truncate max-w-[280px]">
                            {r.filename}
                          </span>
                          {r.content_type && (
                            <span className="text-[10px] text-slate-400">{r.content_type}</span>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <button
                        onClick={() => navigate(`/agents/${r.agent_id}`)}
                        className="text-xs text-blue-600 hover:text-blue-700 hover:underline truncate max-w-[120px] block"
                      >
                        {r.agent_name}
                      </button>
                    </td>
                    <td className="px-3 py-3">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-600">
                        {r.type === 'file' ? 'Dosya' : r.type === 'url' ? 'URL' : r.type}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right text-slate-600 tabular-nums">
                      {r.size > 0 ? formatBytes(r.size) : '-'}
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-400 whitespace-nowrap hidden lg:table-cell">
                      {r.created_at ? new Date(r.created_at).toLocaleDateString('tr-TR') : '-'}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <button
                        onClick={() => handleDelete(r.resource_id)}
                        className="px-2.5 py-1 text-xs text-red-600 bg-red-50 rounded-lg hover:bg-red-100 transition-colors"
                      >
                        Sil
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Upload Modal */}
      {showUpload && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
          onClick={() => !uploading && setShowUpload(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-xl w-full max-w-md mx-4 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold text-gray-900 mb-1">Kaynak Yukle</h2>
            <p className="text-sm text-gray-500 mb-5">
              Dosyalari yuklemek istediginiz agent'i secin.
            </p>

            <label className="block text-sm font-medium text-gray-700 mb-1.5">Agent</label>
            <select
              value={selectedAgentId}
              onChange={(e) => setSelectedAgentId(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 mb-4"
            >
              <option value="">Agent secin...</option>
              {agents.map((a) => (
                <option key={a.agent_id} value={a.agent_id}>
                  {a.name}
                </option>
              ))}
            </select>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setShowUpload(false);
                  setSelectedAgentId('');
                }}
                disabled={uploading}
                className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50"
              >
                Iptal
              </button>
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={!selectedAgentId || uploading}
                className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {uploading ? 'Yukleniyor...' : 'Dosya Sec'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
