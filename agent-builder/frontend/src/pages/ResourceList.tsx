import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { resourceApi, Resource, ResourceType, ResourceDetail, FileTree, FileTreeDocument } from '../services/resourceApi';
import Breadcrumb from '../components/Breadcrumb';
import CreateResourceModal from '../components/CreateResourceModal';

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

const extStatusColors: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-600',
  extracting: 'bg-yellow-100 text-yellow-700',
  ready: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
};

const procStatusColors: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-600',
  processing: 'bg-blue-100 text-blue-700',
  ready: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
};

const statusColors: Record<string, string> = {
  created: 'bg-gray-100 text-gray-600',
  uploading: 'bg-yellow-100 text-yellow-700',
  extracting: 'bg-blue-100 text-blue-700',
  ready: 'bg-green-100 text-green-700',
};

const statusLabels: Record<string, string> = {
  created: 'Olusturuldu',
  uploading: 'Yukleniyor',
  extracting: 'Extract Ediliyor',
  ready: 'Hazir',
};

const typeLabels: Record<string, string> = {
  minio: 'Dosya',
  link: 'Web',
  youtube: 'YouTube',
  image: 'Goruntu',
  notebooklm: 'NotebookLM',
};

const typeColors: Record<string, string> = {
  minio: 'bg-slate-100 text-slate-600',
  link: 'bg-cyan-100 text-cyan-700',
  youtube: 'bg-red-100 text-red-700',
  image: 'bg-emerald-100 text-emerald-700',
  notebooklm: 'bg-purple-100 text-purple-700',
};

function TypeIcon({ type, className = 'w-4 h-4' }: { type: string; className?: string }) {
  switch (type) {
    case 'link':
      return <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" /></svg>;
    case 'youtube':
      return <svg className={className} fill="currentColor" viewBox="0 0 24 24"><path d="M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" /></svg>;
    case 'image':
      return <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>;
    case 'notebooklm':
      return <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" /></svg>;
    default:
      return <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" /></svg>;
  }
}

const ZOOM_MIN = 0.25;
const ZOOM_MAX = 5;
const ZOOM_STEP = 0.25;

export default function ResourceList() {
  const [resources, setResources] = useState<Resource[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);

  const [search, setSearch] = useState('');

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ResourceDetail | null>(null);
  const [fileTree, setFileTree] = useState<FileTree | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [expandedDocs, setExpandedDocs] = useState<Set<string>>(new Set());
  const [fileSearch, setFileSearch] = useState('');

  // Image preview modal
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewName, setPreviewName] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [previewImages, setPreviewImages] = useState<{ path: string; name: string }[]>([]);
  const [previewIndex, setPreviewIndex] = useState(0);

  const [uploadingId, setUploadingId] = useState('');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [showUploadMenu, setShowUploadMenu] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    loadResources();
  }, []);

  useEffect(() => {
    if (!showUploadMenu) return;
    const close = () => setShowUploadMenu('');
    document.addEventListener('click', close, { once: true });
    return () => document.removeEventListener('click', close);
  }, [showUploadMenu]);

  async function loadResources() {
    try {
      const res = await resourceApi.list();
      setResources(res.resources);
    } catch (e) {
      console.error('Failed to load resources', e);
    } finally {
      setLoading(false);
    }
  }

  const loadDetail = useCallback(async (id: string) => {
    setDetailLoading(true);
    try {
      const [d, ft] = await Promise.all([
        resourceApi.getDetail(id),
        resourceApi.listFiles(id),
      ]);
      setDetail(d);
      setFileTree(ft);
    } catch (e) {
      console.error('Failed to load detail', e);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  function toggleExpand(id: string) {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      setFileTree(null);
      setExpandedDocs(new Set());
      setFileSearch('');
    } else {
      setExpandedId(id);
      setFileSearch('');
      loadDetail(id);
    }
  }

  function toggleDocExpand(docId: string) {
    setExpandedDocs((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  }

  useEffect(() => {
    if (!expandedId || !detail) return;
    if (detail.status !== 'extracting') return;

    const interval = setInterval(async () => {
      try {
        const [d, ft] = await Promise.all([
          resourceApi.getDetail(expandedId),
          resourceApi.listFiles(expandedId),
        ]);
        setDetail(d);
        setFileTree(ft);
        if (d.status !== 'extracting') {
          await loadResources();
        }
      } catch { /* ignore */ }
    }, 5000);
    return () => clearInterval(interval);
  }, [expandedId, detail?.status]);

  const previewResourceIdRef = useRef<string>('');

  async function openImagePreview(
    resourceId: string,
    images: { path: string; name: string }[],
    index: number,
  ) {
    previewResourceIdRef.current = resourceId;
    setPreviewImages(images);
    setPreviewIndex(index);
    await loadPreviewImage(resourceId, images[index]);
  }

  async function loadPreviewImage(resourceId: string, img: { path: string; name: string }) {
    setPreviewName(img.name);
    setPreviewLoading(true);
    setPreviewUrl(null);
    setZoom(1);
    try {
      const res = await resourceApi.getPreviewUrl(resourceId, img.path);
      setPreviewUrl(res.url);
    } catch (e) {
      console.error('Failed to get preview URL', e);
    } finally {
      setPreviewLoading(false);
    }
  }

  function goToImage(dir: 1 | -1) {
    const newIdx = previewIndex + dir;
    if (newIdx < 0 || newIdx >= previewImages.length) return;
    setPreviewIndex(newIdx);
    loadPreviewImage(previewResourceIdRef.current, previewImages[newIdx]);
  }

  function closePreview() {
    setPreviewUrl(null);
    setPreviewName('');
    setZoom(1);
    setPreviewImages([]);
    setPreviewIndex(0);
  }

  useEffect(() => {
    if (!previewUrl && !previewLoading) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closePreview();
      if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(z + ZOOM_STEP, ZOOM_MAX));
      if (e.key === '-') setZoom((z) => Math.max(z - ZOOM_STEP, ZOOM_MIN));
      if (e.key === '0') setZoom(1);
      if (e.key === 'ArrowLeft') goToImage(-1);
      if (e.key === 'ArrowRight') goToImage(1);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [previewUrl, previewLoading, previewIndex, previewImages]);

  function handleWheel(e: React.WheelEvent) {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const delta = e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP;
      setZoom((z) => Math.max(ZOOM_MIN, Math.min(z + delta, ZOOM_MAX)));
    }
  }

  async function handleModalCreate(data: {
    name: string;
    type: ResourceType;
    description: string;
    url?: string;
    notebook_id?: string;
    metadata: Record<string, any>;
    uploadMode?: 'file' | 'folder';
  }) {
    setCreating(true);
    try {
      const res = await resourceApi.create({
        name: data.name,
        type: data.type,
        description: data.description,
        url: data.url,
        notebook_id: data.notebook_id,
        metadata: data.metadata,
      });
      setShowCreate(false);
      await loadResources();
      if (data.uploadMode) {
        triggerUpload(res.id, data.uploadMode);
      }
    } catch (e) {
      console.error('Create failed', e);
    } finally {
      setCreating(false);
    }
  }

  async function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const rawFiles = e.target.files;
    if (!rawFiles?.length || !uploadingId) return;

    const files = filterAcceptedFiles(Array.from(rawFiles));
    if (!files.length) {
      alert('Secilen klasorde desteklenen dosya bulunamadi (PDF, goruntu, metin).');
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (folderInputRef.current) folderInputRef.current.value = '';
      setUploadingId('');
      return;
    }

    try {
      setUploadProgress(0);
      await resourceApi.upload(uploadingId, files, (pct) => setUploadProgress(pct));
      setUploadProgress(100);
      await loadResources();
    } catch (err) {
      console.error('Upload failed', err);
    } finally {
      setTimeout(() => {
        setUploadingId('');
        setUploadProgress(0);
      }, 1500);
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (folderInputRef.current) folderInputRef.current.value = '';
    }
  }

  function triggerUpload(resourceId: string, mode: 'file' | 'folder') {
    setUploadingId(resourceId);
    setShowUploadMenu('');
    if (mode === 'folder') {
      folderInputRef.current?.click();
    } else {
      fileInputRef.current?.click();
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('Bu resource silinecek. Emin misiniz?')) return;
    try {
      await resourceApi.remove(id);
      await loadResources();
    } catch (e) {
      console.error('Delete failed', e);
    }
  }

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto p-6">
        <div className="text-center py-12 text-gray-400">Yukleniyor...</div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-6">
      <input type="file" ref={fileInputRef} className="hidden" multiple accept=".pdf,.png,.jpg,.jpeg,.tiff,.tif,.txt,.md" onChange={handleFileSelect} />
      <input type="file" ref={folderInputRef} className="hidden"
        /* @ts-expect-error webkitdirectory is non-standard but widely supported */
        webkitdirectory="" directory="" multiple onChange={handleFileSelect}
      />

      {/* Breadcrumb */}
      <Breadcrumb items={[
        { label: 'Dashboard', to: '/dashboard' },
        { label: 'Resources' },
      ]} />

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Resources</h1>
          <p className="text-sm text-gray-500 mt-1">
            Belge koleksiyonlarini yonetin. PDF yukleyin, image extraction otomatik yapilir.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowCreate(true)} className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700">+ Yeni Resource</button>
        </div>
      </div>

      {/* Upload Progress */}
      {uploadingId && uploadProgress > 0 && (
        <div className="mb-4 p-3 bg-blue-50 rounded-lg">
          <div className="flex items-center justify-between text-sm text-blue-700 mb-1">
            <span>Yukleniyor...</span><span>{uploadProgress}%</span>
          </div>
          <div className="w-full bg-blue-200 rounded-full h-2">
            <div className="bg-blue-600 h-2 rounded-full transition-all duration-300" style={{ width: `${uploadProgress}%` }} />
          </div>
        </div>
      )}

      {/* Search */}
      {resources.length > 0 && (
        <div className="mb-4 relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Resource ara... (isim, durum)"
            className="w-full pl-10 pr-8 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" />
          {search && (
            <button onClick={() => setSearch('')} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      )}

      {/* Resource List */}
      {resources.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl border border-gray-200">
          <div className="text-4xl mb-3">📂</div>
          <h3 className="text-lg font-medium text-gray-700 mb-1">Henuz resource yok</h3>
          <p className="text-sm text-gray-500 mb-4">PDF belgelerinizi yuklemek icin yeni bir resource olusturun.</p>
          <button onClick={() => setShowCreate(true)} className="px-4 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700">+ Yeni Resource</button>
        </div>
      ) : (() => {
        const q = search.toLowerCase().trim();
        const filtered = q
          ? resources.filter((r) => r.name.toLowerCase().includes(q) || (r.description || '').toLowerCase().includes(q) || (statusLabels[r.status] || r.status).toLowerCase().includes(q) || r.status.toLowerCase().includes(q))
          : resources;
        return filtered.length === 0 && q ? (
          <div className="text-center py-12 bg-white rounded-xl border border-gray-200">
            <p className="text-sm text-gray-500">"<span className="font-medium text-gray-700">{search}</span>" ile eslesen resource bulunamadi.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {filtered.map((r) => {
              const isExpanded = expandedId === r.id;
              return (
                <div key={r.id} className={`bg-white border rounded-xl transition-shadow ${isExpanded ? 'border-blue-300 shadow-md' : 'border-gray-200 hover:shadow-sm'}`}>
                  <div className="flex items-center justify-between p-4 cursor-pointer" onClick={() => toggleExpand(r.id)}>
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <svg className={`w-4 h-4 text-gray-400 transition-transform ${isExpanded ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                        </svg>
                        <span className="text-gray-400"><TypeIcon type={r.type} className="w-4 h-4" /></span>
                        <h3 className="font-medium text-gray-900">{r.name}</h3>
                        <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${typeColors[r.type] || 'bg-gray-100 text-gray-600'}`}>{typeLabels[r.type] || r.type}</span>
                        <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${statusColors[r.status] || 'bg-gray-100'}`}>{statusLabels[r.status] || r.status}</span>
                      </div>
                      {r.description && (
                        <p className="text-xs text-gray-500 ml-6 mb-1">{r.description}</p>
                      )}
                      <div className="flex items-center gap-4 text-xs text-gray-500 ml-6">
                        {r.type === 'minio' && <span>{r.total_documents} belge</span>}
                        {r.type === 'minio' && r.total_documents > 0 && <span>{r.extracted_documents}/{r.total_documents} extract edildi</span>}
                        {(r.type === 'link' || r.type === 'youtube') && r.metadata?.url && (
                          <span className="text-blue-500 truncate max-w-xs" title={r.metadata.url}>{r.metadata.url}</span>
                        )}
                        {r.type === 'notebooklm' && r.metadata?.notebook_id && (
                          <span className="text-purple-500">Notebook: {r.metadata.notebook_id.slice(0, 8)}...</span>
                        )}
                        {r.workspace_id && <span className="text-blue-600">Workspace'e bagli</span>}
                        {r.created_at && <span>{new Date(r.created_at).toLocaleDateString('tr-TR')}</span>}
                      </div>
                      {r.total_documents > 0 && r.status === 'extracting' && (
                        <div className="mt-2 w-48 ml-6">
                          <div className="w-full bg-gray-200 rounded-full h-1.5">
                            <div className="bg-blue-500 h-1.5 rounded-full transition-all" style={{ width: `${Math.round((r.extracted_documents / r.total_documents) * 100)}%` }} />
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                      {(r.type === 'minio' || r.type === 'image') && (
                        <div className="relative">
                          <button onClick={(e) => { e.stopPropagation(); setShowUploadMenu(showUploadMenu === r.id ? '' : r.id); }}
                            className="px-3 py-1.5 text-xs text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 flex items-center gap-1">
                            Yukle
                            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
                          </button>
                          {showUploadMenu === r.id && (
                            <div className="absolute right-0 mt-1 w-44 bg-white border border-gray-200 rounded-lg shadow-lg z-10">
                              <button onClick={() => triggerUpload(r.id, 'file')} className="w-full text-left px-3 py-2 text-xs text-gray-700 hover:bg-gray-50 rounded-t-lg flex items-center gap-2">
                                <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" /></svg>
                                Dosya Sec
                              </button>
                              <button onClick={() => triggerUpload(r.id, 'folder')} className="w-full text-left px-3 py-2 text-xs text-gray-700 hover:bg-gray-50 rounded-b-lg flex items-center gap-2">
                                <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" /></svg>
                                Klasor Sec
                              </button>
                            </div>
                          )}
                        </div>
                      )}
                      <button onClick={() => handleDelete(r.id)} className="px-3 py-1.5 text-xs text-red-600 bg-red-50 rounded-lg hover:bg-red-100">Sil</button>
                    </div>
                  </div>

                  {/* Inline Expandable Detail */}
                  {isExpanded && (
                    <div className="border-t border-gray-100 px-4 pb-4">
                      {detailLoading && !detail ? (
                        <div className="py-6 text-center text-sm text-gray-400">Detaylar yukleniyor...</div>
                      ) : detail ? (
                        <div className="pt-4 space-y-4">
                          {/* Status Breakdown */}
                          <div className="grid grid-cols-2 gap-3">
                            <div className="bg-gray-50 rounded-lg p-3">
                              <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Extraction Durumu</h4>
                              <div className="flex flex-wrap gap-2">
                                {Object.entries(detail.status_breakdown?.extraction || {}).map(([s, c]) => (
                                  <div key={s} className="flex items-center gap-1.5">
                                    <span className={`inline-block w-2 h-2 rounded-full ${s === 'ready' ? 'bg-green-500' : s === 'failed' ? 'bg-red-500' : s === 'extracting' ? 'bg-yellow-500' : 'bg-gray-400'}`} />
                                    <span className="text-xs text-gray-700 capitalize">{s}</span>
                                    <span className="text-xs font-semibold text-gray-900">{c}</span>
                                  </div>
                                ))}
                                {Object.keys(detail.status_breakdown?.extraction || {}).length === 0 && <span className="text-xs text-gray-400">Henuz belge yok</span>}
                              </div>
                            </div>
                            <div className="bg-gray-50 rounded-lg p-3">
                              <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Processing Durumu</h4>
                              <div className="flex flex-wrap gap-2">
                                {Object.entries(detail.status_breakdown?.processing || {}).map(([s, c]) => (
                                  <div key={s} className="flex items-center gap-1.5">
                                    <span className={`inline-block w-2 h-2 rounded-full ${s === 'ready' ? 'bg-green-500' : s === 'failed' ? 'bg-red-500' : s === 'processing' ? 'bg-blue-500' : 'bg-gray-400'}`} />
                                    <span className="text-xs text-gray-700 capitalize">{s}</span>
                                    <span className="text-xs font-semibold text-gray-900">{c}</span>
                                  </div>
                                ))}
                                {Object.keys(detail.status_breakdown?.processing || {}).length === 0 && <span className="text-xs text-gray-400">Henuz belge yok</span>}
                              </div>
                            </div>
                          </div>

                          {/* File Tree */}
                          {fileTree && fileTree.documents.length > 0 ? (() => {
                            const fq = fileSearch.toLowerCase().trim();
                            const filteredDocs = fq
                              ? fileTree.documents.filter((doc) =>
                                  doc.file_name.toLowerCase().includes(fq) ||
                                  doc.extracted_images.some((img) => img.name.toLowerCase().includes(fq))
                                )
                              : fileTree.documents;
                            return (
                            <div className="bg-gray-50 rounded-lg p-3 font-mono text-xs">
                              <div className="flex items-center justify-between mb-2">
                                <div className="flex items-center gap-1.5 text-gray-500 font-sans font-medium text-[11px] uppercase tracking-wide">
                                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" /></svg>
                                  Dosya Yapisi
                                </div>
                                <div className="relative font-sans">
                                  <svg className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                                  </svg>
                                  <input
                                    type="text"
                                    value={fileSearch}
                                    onChange={(e) => setFileSearch(e.target.value)}
                                    placeholder="Dosya ara..."
                                    className="pl-7 pr-6 py-1 text-[11px] border border-gray-200 rounded bg-white focus:outline-none focus:ring-1 focus:ring-blue-400 w-44"
                                  />
                                  {fileSearch && (
                                    <button onClick={() => setFileSearch('')} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                                      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                                    </button>
                                  )}
                                </div>
                              </div>
                              {filteredDocs.length === 0 && fq ? (
                                <div className="text-center py-3 text-gray-400 font-sans text-[11px]">
                                  "<span className="text-gray-600">{fileSearch}</span>" ile eslesen dosya bulunamadi.
                                </div>
                              ) : (
                              <div className="space-y-0.5">
                                {filteredDocs.map((doc: FileTreeDocument, idx: number) => {
                                  const fqInner = fileSearch.toLowerCase().trim();
                                  const visibleImages = fqInner
                                    ? doc.extracted_images.filter((img) => img.name.toLowerCase().includes(fqInner))
                                    : doc.extracted_images;
                                  const docNameMatches = doc.file_name.toLowerCase().includes(fqInner);
                                  const showAllImages = docNameMatches || !fqInner;
                                  const displayImages = showAllImages ? doc.extracted_images : visibleImages;
                                  const isDocExpanded = expandedDocs.has(doc.id) || (!!fqInner && !docNameMatches && visibleImages.length > 0);
                                  const hasImages = displayImages.length > 0;
                                  const isLast = idx === filteredDocs.length - 1;
                                  return (
                                    <div key={doc.id}>
                                      <div className={`flex items-start gap-0 py-0.5 rounded ${hasImages ? 'cursor-pointer hover:bg-gray-100' : ''}`}
                                        onClick={() => hasImages && toggleDocExpand(doc.id)}>
                                        <span className="text-gray-300 select-none shrink-0 w-5">{isLast ? '└' : '├'}</span>
                                        {hasImages ? (
                                          <svg className={`w-3 h-3 mt-0.5 mr-1 text-gray-400 transition-transform shrink-0 ${isDocExpanded ? 'rotate-90' : ''}`}
                                            fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                                          </svg>
                                        ) : <span className="w-4 shrink-0" />}
                                        <span className="mr-1.5 mt-px shrink-0">
                                          {doc.file_type === 'pdf'
                                            ? <svg className="w-3.5 h-3.5 text-red-400" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clipRule="evenodd"/></svg>
                                            : <svg className="w-3.5 h-3.5 text-emerald-400" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M4 3a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V5a2 2 0 00-2-2H4zm12 12H4l4-8 3 6 2-4 3 6z" clipRule="evenodd"/></svg>
                                          }
                                        </span>
                                        <div className="flex-1 min-w-0">
                                          <div className="flex items-center gap-2 flex-wrap">
                                            <span className="text-gray-800 break-all">{doc.file_name}</span>
                                            <span className="text-gray-400 font-sans shrink-0">{formatBytes(doc.file_size)}</span>
                                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-sans font-medium shrink-0 ${extStatusColors[doc.extraction_status] || 'bg-gray-100 text-gray-600'}`}>{doc.extraction_status}</span>
                                            <span className={`px-1.5 py-0.5 rounded text-[10px] font-sans font-medium shrink-0 ${procStatusColors[doc.processing_status] || 'bg-gray-100 text-gray-600'}`}>{doc.processing_status}</span>
                                            {doc.page_count > 0 && <span className="text-gray-400 font-sans shrink-0">{doc.page_count} sayfa</span>}
                                            {doc.confidence_score > 0 && <span className="text-gray-400 font-sans shrink-0">{(doc.confidence_score * 100).toFixed(0)}%</span>}
                                          </div>
                                          {doc.error_message && <div className="text-red-500 font-sans text-[11px] mt-0.5">{doc.error_message}</div>}
                                        </div>
                                      </div>

                                      {isDocExpanded && hasImages && (
                                        <div className="ml-5">
                                          <div className="flex items-center gap-1.5 py-0.5">
                                            <span className="text-gray-300 select-none shrink-0 w-5">{isLast ? ' ' : '│'}</span>
                                            <svg className="w-3 h-3 text-amber-400 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"/></svg>
                                            <span className="text-amber-600 font-sans text-[11px] font-medium">images/ ({displayImages.length} dosya)</span>
                                          </div>
                                          {displayImages.map((img, imgIdx) => {
                                            const isImgLast = imgIdx === displayImages.length - 1;
                                            return (
                                              <div key={img.name}
                                                className="flex items-center gap-1.5 py-px cursor-pointer hover:bg-blue-50 rounded px-0.5 group"
                                                onClick={() => expandedId && openImagePreview(expandedId, displayImages, imgIdx)}
                                              >
                                                <span className="text-gray-300 select-none shrink-0 w-5">{isLast ? ' ' : '│'}</span>
                                                <span className="text-gray-300 select-none shrink-0 w-5">{isImgLast ? '└' : '├'}</span>
                                                <svg className="w-3 h-3 text-emerald-400 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M4 3a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V5a2 2 0 00-2-2H4zm12 12H4l4-8 3 6 2-4 3 6z" clipRule="evenodd"/></svg>
                                                <span className="text-gray-700 break-all group-hover:text-blue-700">{img.name}</span>
                                                <span className="text-gray-400 font-sans shrink-0">{formatBytes(img.size)}</span>
                                                <svg className="w-3 h-3 text-gray-300 group-hover:text-blue-500 shrink-0 ml-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" />
                                                </svg>
                                              </div>
                                            );
                                          })}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                              )}
                            </div>
                            );
                          })() : (
                            <div className="text-center py-4 text-xs text-gray-400">Henuz belge yuklenmemis.</div>
                          )}

                          <div className="flex justify-end">
                            <button onClick={() => loadDetail(r.id)} disabled={detailLoading}
                              className="px-3 py-1 text-xs text-gray-500 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50">
                              {detailLoading ? 'Yukleniyor...' : 'Yenile'}
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        );
      })()}

      {/* Create Resource Modal */}
      <CreateResourceModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreate={handleModalCreate}
        creating={creating}
      />

      {/* Image Preview Modal */}
      {(previewUrl || previewLoading) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={closePreview}>
          <div className="relative flex flex-col items-center max-w-[95vw] max-h-[95vh]" onClick={(e) => e.stopPropagation()}>
            {/* Header bar */}
            <div className="flex items-center justify-between w-full bg-black/50 rounded-t-xl px-4 py-2 min-w-[300px]">
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-white text-sm truncate">{previewName}</span>
                {previewImages.length > 1 && (
                  <span className="text-white/50 text-xs shrink-0">{previewIndex + 1} / {previewImages.length}</span>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {previewImages.length > 1 && (
                  <>
                    <button onClick={() => goToImage(-1)} disabled={previewIndex === 0}
                      className="p-1.5 text-white/70 hover:text-white hover:bg-white/10 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed" title="Onceki (←)">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
                    </button>
                    <button onClick={() => goToImage(1)} disabled={previewIndex === previewImages.length - 1}
                      className="p-1.5 text-white/70 hover:text-white hover:bg-white/10 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed" title="Sonraki (→)">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                    </button>
                    <div className="w-px h-4 bg-white/20 mx-1" />
                  </>
                )}
                <button onClick={() => setZoom((z) => Math.max(z - ZOOM_STEP, ZOOM_MIN))}
                  className="p-1.5 text-white/70 hover:text-white hover:bg-white/10 rounded transition-colors" title="Uzaklastir (-)">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM13 10H7" /></svg>
                </button>
                <button onClick={() => setZoom(1)}
                  className="px-2 py-0.5 text-xs text-white/80 hover:text-white bg-white/10 hover:bg-white/20 rounded transition-colors font-mono min-w-[48px] text-center">
                  {Math.round(zoom * 100)}%
                </button>
                <button onClick={() => setZoom((z) => Math.min(z + ZOOM_STEP, ZOOM_MAX))}
                  className="p-1.5 text-white/70 hover:text-white hover:bg-white/10 rounded transition-colors" title="Yakinlastir (+)">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" /></svg>
                </button>
                <div className="w-px h-4 bg-white/20 mx-1" />
                <button onClick={closePreview} className="p-1.5 text-white/70 hover:text-white hover:bg-white/10 rounded transition-colors" title="Kapat (ESC)">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
              </div>
            </div>

            {/* Image area */}
            <div className="overflow-auto bg-gray-900/50 rounded-b-xl" style={{ maxWidth: '90vw', maxHeight: 'calc(90vh - 40px)' }}
              onWheel={handleWheel}>
              {previewLoading ? (
                <div className="flex items-center justify-center w-[400px] h-[300px]">
                  <div className="text-white/60 text-sm">Gorsel yukleniyor...</div>
                </div>
              ) : previewUrl ? (
                <img
                  src={previewUrl}
                  alt={previewName}
                  className="block transition-transform duration-150"
                  style={{ transform: `scale(${zoom})`, transformOrigin: 'top left' }}
                  draggable={false}
                />
              ) : null}
            </div>
          </div>

          {/* Side navigation arrows for multi-image */}
          {previewImages.length > 1 && (
            <>
              {previewIndex > 0 && (
                <button onClick={(e) => { e.stopPropagation(); goToImage(-1); }}
                  className="absolute left-4 top-1/2 -translate-y-1/2 p-3 bg-black/40 hover:bg-black/60 text-white/80 hover:text-white rounded-full transition-colors backdrop-blur-sm">
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
                </button>
              )}
              {previewIndex < previewImages.length - 1 && (
                <button onClick={(e) => { e.stopPropagation(); goToImage(1); }}
                  className="absolute right-4 top-1/2 -translate-y-1/2 p-3 bg-black/40 hover:bg-black/60 text-white/80 hover:text-white rounded-full transition-colors backdrop-blur-sm">
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
