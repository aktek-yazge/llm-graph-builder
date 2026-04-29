import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import Breadcrumb from '../components/Breadcrumb';
import {
  listGlobalResources,
  uploadGlobalResource,
  deleteGlobalResource,
  listAgents,
  type GlobalResourceItem,
  type AgentInfo,
} from '../services/evolvingApi';

const ACCEPTED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.tif', '.txt', '.md'];

function filterAcceptedFiles(files: File[]): File[] {
  return files.filter((f) => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
    return ACCEPTED_EXTENSIONS.includes(ext);
  });
}

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

type SortKey = 'filename' | 'agent_name' | 'size' | 'created_at';
type SortDir = 'asc' | 'desc';

interface ResourceRow extends GlobalResourceItem {
  _uid: string;
}

let _uidCounter = 0;

export default function ResourcesPage() {
  const [resources, setResources] = useState<ResourceRow[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [agentFilter, setAgentFilter] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('created_at');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const [showUpload, setShowUpload] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await listGlobalResources();
      const rows: ResourceRow[] = data.resources.map((r) => ({
        ...r,
        _uid: `${r.resource_id || ''}_${r.agent_id}_${++_uidCounter}`,
      }));
      setResources(rows);
    } catch (e) {
      console.error('Failed to load resources', e);
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
    load();
    loadAgents();
  }, [load, loadAgents]);

  // --- Filtering ---
  const q = search.toLowerCase().trim();
  const filtered = useMemo(() => {
    let list = resources;
    if (agentFilter) {
      list = list.filter((r) => r.agent_id === agentFilter);
    }
    if (q) {
      list = list.filter(
        (r) =>
          r.filename.toLowerCase().includes(q) ||
          r.agent_name.toLowerCase().includes(q) ||
          r.content_type.toLowerCase().includes(q),
      );
    }
    return list;
  }, [resources, agentFilter, q]);

  // --- Sorting ---
  const sorted = useMemo(() => {
    const list = [...filtered];
    list.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case 'filename':
          cmp = a.filename.localeCompare(b.filename, 'tr');
          break;
        case 'agent_name':
          cmp = a.agent_name.localeCompare(b.agent_name, 'tr');
          break;
        case 'size':
          cmp = (a.size || 0) - (b.size || 0);
          break;
        case 'created_at':
          cmp = (a.created_at || '').localeCompare(b.created_at || '');
          break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return list;
  }, [filtered, sortKey, sortDir]);

  // --- Agent groups for filter ---
  const agentGroups = useMemo(() => {
    const map = new Map<string, { name: string; count: number }>();
    for (const r of resources) {
      const g = map.get(r.agent_id);
      if (g) g.count++;
      else map.set(r.agent_id, { name: r.agent_name, count: 1 });
    }
    return map;
  }, [resources]);

  const totalSize = resources.reduce((s, r) => s + (r.size || 0), 0);

  // --- Selection ---
  const allVisibleIds = useMemo(() => sorted.map((r) => r._uid), [sorted]);
  const allSelected = allVisibleIds.length > 0 && allVisibleIds.every((id) => selected.has(id));
  const someSelected = selected.size > 0;

  function toggleSelectAll() {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(allVisibleIds));
    }
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // --- Sort handler ---
  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  }

  // --- Single delete ---
  async function handleDelete(uid: string, resourceId: string) {
    if (!confirm('Bu kaynak silinecek. Emin misiniz?')) return;
    try {
      await deleteGlobalResource(resourceId);
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(uid);
        return next;
      });
      await load();
    } catch (e) {
      console.error('Delete failed', e);
    }
  }

  // --- Bulk delete ---
  async function handleBulkDelete() {
    const count = selected.size;
    if (!confirm(`${count} kaynak silinecek. Emin misiniz?`)) return;
    setBulkDeleting(true);
    try {
      const uidToResourceId = new Map(resources.map((r) => [r._uid, r.resource_id]));
      for (const uid of selected) {
        const realId = uidToResourceId.get(uid);
        if (realId) await deleteGlobalResource(realId);
      }
      setSelected(new Set());
      await load();
    } catch (e) {
      console.error('Bulk delete failed', e);
    } finally {
      setBulkDeleting(false);
    }
  }

  // --- Upload ---
  async function doUpload(files: File[]) {
    const accepted = filterAcceptedFiles(files);
    if (!accepted.length) {
      alert('Secilen dosyalar arasinda desteklenen format bulunamadi.\nDesteklenen: ' + ACCEPTED_EXTENSIONS.join(', '));
      return;
    }
    if (!selectedAgentId) {
      alert('Lutfen bir agent secin.');
      return;
    }
    setUploading(true);
    try {
      await uploadGlobalResource(selectedAgentId, accepted);
      setShowUpload(false);
      setSelectedAgentId('');
      await load();
    } catch (err) {
      console.error('Upload failed', err);
      alert('Yukleme sirasinda hata olustu.');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const rawFiles = e.target.files;
    if (!rawFiles?.length) return;
    doUpload(Array.from(rawFiles));
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) doUpload(files);
  }

  return (
    <div className="min-h-screen bg-slate-50/80">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <input
          type="file"
          ref={fileInputRef}
          className="hidden"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(',')}
          onChange={handleFileSelect}
        />

        <Breadcrumb items={[{ label: 'Dashboard', to: '/dashboard' }, { label: 'Kaynaklar' }]} />

        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Kaynaklar</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              Tum agentlara ait belgeler ve dosyalar
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              disabled={loading}
              className="px-3.5 py-2 text-sm text-slate-600 bg-white border border-slate-200 rounded-xl
                         hover:bg-slate-50 transition-colors disabled:opacity-50"
            >
              Yenile
            </button>
            <button
              onClick={() => setShowUpload(true)}
              className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white rounded-xl
                         hover:bg-blue-700 text-sm font-medium transition-all shadow-sm hover:shadow-md
                         active:scale-[0.97]"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Kaynak Yukle
            </button>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          <StatCard label="Toplam Kaynak" value={resources.length} accent="blue" />
          <StatCard label="Agent" value={agentGroups.size} accent="violet" />
          <StatCard label="Toplam Boyut" value={formatSize(totalSize)} accent="emerald" />
          <StatCard
            label="Secili"
            value={selected.size}
            accent={selected.size > 0 ? 'amber' : 'slate'}
          />
        </div>

        {/* Toolbar: Search + Agent Filter */}
        {resources.length > 0 && (
          <div className="flex flex-col sm:flex-row gap-3 mb-4">
            <div className="relative flex-1">
              <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Dosya adi, agent veya tur ile ara..."
                className="w-full pl-10 pr-8 py-2.5 text-sm border border-slate-200 rounded-xl bg-white
                           focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400
                           placeholder:text-slate-400 transition-all"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>

            <select
              value={agentFilter}
              onChange={(e) => setAgentFilter(e.target.value)}
              className="px-3.5 py-2.5 text-sm border border-slate-200 rounded-xl bg-white
                         focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400
                         transition-all min-w-[180px]"
            >
              <option value="">Tum Agent'lar</option>
              {Array.from(agentGroups.entries()).map(([agentId, info]) => (
                <option key={agentId} value={agentId}>
                  {info.name} ({info.count})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Bulk Action Bar */}
        <AnimatePresence>
          {someSelected && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="mb-4 flex items-center justify-between gap-3 px-5 py-3
                         bg-amber-50 border border-amber-200 rounded-xl"
            >
              <span className="text-sm font-medium text-amber-800">
                {selected.size} kaynak secili
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setSelected(new Set())}
                  className="px-3 py-1.5 text-xs text-slate-600 bg-white border border-slate-200
                             rounded-lg hover:bg-slate-50 transition-colors"
                >
                  Secimi Kaldir
                </button>
                <button
                  onClick={handleBulkDelete}
                  disabled={bulkDeleting}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium
                             text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors
                             disabled:opacity-50"
                >
                  {bulkDeleting ? (
                    <div className="animate-spin rounded-full h-3 w-3 border-2 border-white/30 border-t-white" />
                  ) : (
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  )}
                  Topluca Sil
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Content */}
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="animate-spin rounded-full h-8 w-8 border-2 border-blue-200 border-t-blue-600" />
          </div>
        ) : resources.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-2xl border border-slate-200/80 shadow-sm">
            <div className="w-14 h-14 mx-auto mb-3 rounded-full bg-slate-100 flex items-center justify-center">
              <svg className="w-7 h-7 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-slate-700 mb-1">Henuz kaynak yok</h3>
            <p className="text-sm text-slate-500 mb-4">
              Agent'lariniza dosya yukleyerek baslayabilirsiniz.
            </p>
            <button
              onClick={() => setShowUpload(true)}
              className="px-4 py-2 text-sm text-white bg-blue-600 rounded-xl hover:bg-blue-700 transition-colors"
            >
              + Kaynak Yukle
            </button>
          </div>
        ) : (
          <div className="bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50/70">
                  <th className="w-10 px-4 py-3">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleSelectAll}
                      className="rounded border-slate-300 text-blue-600 focus:ring-blue-500/20"
                    />
                  </th>
                  <SortHeader
                    label="Dosya"
                    sortKey="filename"
                    currentKey={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                    align="left"
                  />
                  <SortHeader
                    label="Agent"
                    sortKey="agent_name"
                    currentKey={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                    align="left"
                  />
                  <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Tur</th>
                  <SortHeader
                    label="Boyut"
                    sortKey="size"
                    currentKey={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                    align="right"
                  />
                  <SortHeader
                    label="Tarih"
                    sortKey="created_at"
                    currentKey={sortKey}
                    dir={sortDir}
                    onSort={handleSort}
                    align="left"
                    className="hidden lg:table-cell"
                  />
                  <th className="w-16 px-3 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {sorted.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-5 py-12 text-center text-sm text-slate-400">
                      Sonuc bulunamadi.
                    </td>
                  </tr>
                ) : (
                  sorted.map((r) => (
                    <tr
                      key={r._uid}
                      className={`transition-colors group ${
                        selected.has(r._uid)
                          ? 'bg-blue-50/50'
                          : 'hover:bg-slate-50/50'
                      }`}
                    >
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selected.has(r._uid)}
                          onChange={() => toggleSelect(r._uid)}
                          className="rounded border-slate-300 text-blue-600 focus:ring-blue-500/20"
                        />
                      </td>
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
                          {r.type === 'file' ? 'Dosya' : r.type === 'url' ? 'URL' : r.type}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-right text-slate-600 tabular-nums text-xs">
                        {formatSize(r.size)}
                      </td>
                      <td className="px-3 py-3 text-xs text-slate-400 hidden lg:table-cell whitespace-nowrap">
                        {formatDate(r.created_at)}
                      </td>
                      <td className="px-3 py-3 text-center">
                        <button
                          onClick={() => handleDelete(r._uid, r.resource_id)}
                          title="Sil"
                          className="p-1.5 rounded-lg text-slate-300 hover:text-red-500 hover:bg-red-50
                                     opacity-0 group-hover:opacity-100 transition-all"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>

            {/* Footer with count */}
            {sorted.length > 0 && (
              <div className="px-5 py-2.5 border-t border-slate-100 bg-slate-50/50">
                <span className="text-xs text-slate-400">
                  {filtered.length === resources.length
                    ? `${resources.length} kaynak`
                    : `${filtered.length} / ${resources.length} kaynak gosteriliyor`}
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Upload Modal */}
      <AnimatePresence>
        {showUpload && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
            onClick={() => !uploading && setShowUpload(false)}
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
                <h2 className="text-lg font-semibold text-slate-900">Kaynak Yukle</h2>
                <p className="text-sm text-slate-500 mt-0.5">
                  Dosyalari yuklemek istediginiz agent'i secin
                </p>
              </div>

              <div className="px-6 py-5 space-y-4">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">
                    Agent <span className="text-red-400">*</span>
                  </label>
                  <select
                    value={selectedAgentId}
                    onChange={(e) => setSelectedAgentId(e.target.value)}
                    className="w-full px-3.5 py-2.5 text-sm border border-slate-200 rounded-xl bg-white
                               focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-400
                               transition-all"
                  >
                    <option value="">Agent secin...</option>
                    {agents.map((a) => (
                      <option key={a.agent_id} value={a.agent_id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Drag & Drop Zone */}
                <div
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={handleDrop}
                  className={`
                    relative border-2 border-dashed rounded-xl p-8 text-center transition-all cursor-pointer
                    ${dragOver
                      ? 'border-blue-400 bg-blue-50/50'
                      : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50/50'
                    }
                    ${!selectedAgentId ? 'opacity-50 pointer-events-none' : ''}
                  `}
                  onClick={() => selectedAgentId && fileInputRef.current?.click()}
                >
                  <div className="flex flex-col items-center gap-2">
                    <div className={`w-10 h-10 rounded-full flex items-center justify-center ${
                      dragOver ? 'bg-blue-100' : 'bg-slate-100'
                    }`}>
                      <svg className={`w-5 h-5 ${dragOver ? 'text-blue-600' : 'text-slate-400'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                      </svg>
                    </div>
                    <div>
                      <p className="text-sm font-medium text-slate-700">
                        {dragOver ? 'Dosyalari birakin' : 'Dosyalari surukleyin veya tiklayin'}
                      </p>
                      <p className="text-xs text-slate-400 mt-1">
                        PDF, PNG, JPG, TIFF, TXT, MD
                      </p>
                    </div>
                  </div>
                </div>

                {uploading && (
                  <div className="flex items-center gap-2 text-sm text-blue-600">
                    <div className="animate-spin rounded-full h-4 w-4 border-2 border-blue-200 border-t-blue-600" />
                    Yukleniyor...
                  </div>
                )}
              </div>

              <div className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex items-center justify-end gap-2.5">
                <button
                  onClick={() => { setShowUpload(false); setSelectedAgentId(''); }}
                  disabled={uploading}
                  className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-800
                             hover:bg-slate-100 rounded-xl transition-colors disabled:opacity-50"
                >
                  Iptal
                </button>
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={!selectedAgentId || uploading}
                  className="inline-flex items-center gap-1.5 px-5 py-2 bg-blue-600 text-white rounded-xl
                             text-sm font-medium hover:bg-blue-700 transition-all shadow-sm
                             disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.97]"
                >
                  {uploading && (
                    <div className="animate-spin rounded-full h-3.5 w-3.5 border-2 border-white/30 border-t-white" />
                  )}
                  Dosya Sec
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ─── Sub-components ─── */

function StatCard({ label, value, accent }: {
  label: string;
  value: number | string;
  accent: string;
}) {
  const accents: Record<string, { text: string }> = {
    blue: { text: 'text-blue-700' },
    violet: { text: 'text-violet-700' },
    emerald: { text: 'text-emerald-700' },
    amber: { text: 'text-amber-700' },
    slate: { text: 'text-slate-400' },
  };
  const a = accents[accent] || accents.slate;

  return (
    <div className="bg-white rounded-2xl border border-slate-200/80 p-4 shadow-sm">
      <p className="text-xs text-slate-500 font-medium">{label}</p>
      <p className={`text-2xl font-bold mt-1 tracking-tight ${a.text}`}>{String(value)}</p>
    </div>
  );
}

function SortHeader({ label, sortKey, currentKey, dir, onSort, align, className = '' }: {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  dir: SortDir;
  onSort: (key: SortKey) => void;
  align: 'left' | 'right';
  className?: string;
}) {
  const active = currentKey === sortKey;
  return (
    <th
      className={`px-3 py-3 text-xs font-medium text-slate-500 cursor-pointer hover:text-slate-700
                  select-none transition-colors ${align === 'right' ? 'text-right' : 'text-left'} ${className}`}
      onClick={() => onSort(sortKey)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && (
          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            {dir === 'asc'
              ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
              : <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            }
          </svg>
        )}
      </span>
    </th>
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
