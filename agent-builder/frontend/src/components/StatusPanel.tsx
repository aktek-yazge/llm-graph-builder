import { useEffect, useState, useRef } from 'react';
import { resourceApi, Resource, ResourceDetail } from '../services/resourceApi';

interface StatusPanelProps {
  workspaceId: string;
  showUpload?: boolean;
  onUploadComplete?: () => void;
}

export default function StatusPanel({ workspaceId, showUpload = false, onUploadComplete }: StatusPanelProps) {
  const [resource, setResource] = useState<ResourceDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    loadResource();
    pollRef.current = setInterval(loadResource, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [workspaceId]);

  async function loadResource() {
    try {
      const list = await resourceApi.list();
      const attached = list.resources.find((r: Resource) => r.workspace_id === workspaceId);
      if (attached) {
        const detail = await resourceApi.getDetail(attached.id, true);
        setResource(detail);
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }

  async function handleFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files?.length || !resource) return;
    setUploading(true);
    try {
      await resourceApi.upload(resource.id, Array.from(files), (pct) => setUploadProgress(pct));
      await loadResource();
      onUploadComplete?.();
    } catch (err) {
      console.error('Upload failed:', err);
    } finally {
      setUploading(false);
      setUploadProgress(0);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  if (loading) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-4 animate-pulse">
        <div className="h-4 bg-gray-200 rounded w-1/2 mb-3" />
        <div className="h-3 bg-gray-200 rounded w-3/4 mb-2" />
        <div className="h-3 bg-gray-200 rounded w-2/3" />
      </div>
    );
  }

  if (!resource) {
    return (
      <div className="bg-gray-50 rounded-xl border border-gray-200 p-4 text-center">
        <p className="text-sm text-gray-500">Henuz resource bagli degil</p>
      </div>
    );
  }

  const ext = resource.status_breakdown?.extraction || {};
  const proc = resource.status_breakdown?.processing || {};

  return (
    <div className="space-y-4">
      {/* Resource Summary */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800">{resource.name}</h3>
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
            resource.status === 'ready' ? 'bg-green-100 text-green-700'
              : resource.status === 'extracting' ? 'bg-yellow-100 text-yellow-700'
                : 'bg-gray-100 text-gray-600'
          }`}>{resource.status}</span>
        </div>

        <div className="grid grid-cols-2 gap-2 text-center">
          <div className="bg-gray-50 rounded-lg p-2">
            <div className="text-lg font-bold text-gray-900">{resource.total_documents}</div>
            <div className="text-xs text-gray-500">Toplam Belge</div>
          </div>
          <div className="bg-green-50 rounded-lg p-2">
            <div className="text-lg font-bold text-green-700">{resource.extracted_documents}</div>
            <div className="text-xs text-green-600">Extracted</div>
          </div>
        </div>
      </div>

      {/* Extraction Breakdown */}
      {Object.keys(ext).length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h4 className="text-xs font-semibold text-gray-600 mb-2 uppercase tracking-wide">Extraction</h4>
          <div className="space-y-1.5">
            {Object.entries(ext).map(([status, count]) => (
              <div key={status} className="flex items-center justify-between text-sm">
                <span className="text-gray-600 capitalize">{status}</span>
                <span className="font-medium text-gray-900">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Processing Breakdown */}
      {Object.keys(proc).length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <h4 className="text-xs font-semibold text-gray-600 mb-2 uppercase tracking-wide">Processing</h4>
          <div className="space-y-1.5">
            {Object.entries(proc).map(([status, count]) => (
              <div key={status} className="flex items-center justify-between text-sm">
                <span className="text-gray-600 capitalize">{status}</span>
                <span className="font-medium text-gray-900">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Bulk Upload Panel */}
      {showUpload && resource && (
        <div className="bg-white rounded-xl border border-indigo-200 p-4">
          <h4 className="text-xs font-semibold text-indigo-700 mb-2 uppercase tracking-wide">Toplu Yukleme</h4>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.tiff,.txt,.md"
            onChange={handleFiles}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="w-full py-3 border-2 border-dashed border-indigo-300 rounded-lg text-sm text-indigo-600 font-medium hover:bg-indigo-50 transition-colors disabled:opacity-50"
          >
            {uploading ? 'Yukleniyor...' : 'Dosya Sec (Toplu)'}
          </button>
          {uploading && uploadProgress > 0 && (
            <div className="mt-2">
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>Yukleniyor</span>
                <span>%{uploadProgress}</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-1.5">
                <div className="bg-indigo-600 h-1.5 rounded-full transition-all" style={{ width: `${uploadProgress}%` }} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
