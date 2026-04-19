import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Breadcrumb from '../components/Breadcrumb';
import {
  getKnowledgeBase,
  type KnowledgeBase,
  type SourceDocument,
} from '../services/platformApi';

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

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

export default function KnowledgeDetailPage() {
  const { endpointId } = useParams<{ endpointId: string }>();
  const navigate = useNavigate();
  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showOntology, setShowOntology] = useState(false);

  const load = useCallback(async () => {
    if (!endpointId) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await getKnowledgeBase(endpointId);
      setKb(data);
    } catch (e: any) {
      setError(e.response?.data?.detail || 'KB yuklenemedi');
    } finally {
      setLoading(false);
    }
  }, [endpointId]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <div className="flex items-center justify-center py-20">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-blue-200 border-t-blue-600" />
        </div>
      </div>
    );
  }

  if (error || !kb) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <Breadcrumb items={[
          { label: 'Dashboard', to: '/dashboard' },
          { label: 'Knowledges', to: '/knowledges' },
          { label: 'Hata' },
        ]} />
        <div className="mt-6 p-6 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
          {error || 'Knowledge Base bulunamadi.'}
        </div>
      </div>
    );
  }

  const ontology = kb.ontology_snapshot || {};
  const entityTypes: any[] = ontology.entity_types || [];
  const relTypes: any[] = ontology.relationship_types || [];

  return (
    <div className="max-w-6xl mx-auto p-6">
      <Breadcrumb items={[
        { label: 'Dashboard', to: '/dashboard' },
        { label: 'Knowledges', to: '/knowledges' },
        { label: kb.name || kb.endpoint_id },
      ]} />

      {/* Header */}
      <div className="flex items-start justify-between mt-2 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">
            {kb.name || kb.agent_name || kb.endpoint_id}
          </h1>
          <p className="text-xs text-gray-400 font-mono mt-1">{kb.endpoint_id}</p>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-sm font-medium ${
            kb.status === 'active'
              ? 'bg-emerald-50 text-emerald-700'
              : 'bg-slate-100 text-slate-500'
          }`}
        >
          <span className={`w-2 h-2 rounded-full ${kb.status === 'active' ? 'bg-emerald-500' : 'bg-slate-400'}`} />
          {kb.status === 'active' ? 'Aktif' : kb.status}
        </span>
      </div>

      {/* Info Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <InfoCard label="Agent" value={kb.agent_name} onClick={() => navigate(`/agents/${kb.agent_id}`)} link />
        <InfoCard label="Workflow" value={kb.workflow_name || '-'} sub={kb.workflow_version > 1 ? `v${kb.workflow_version}` : ''} />
        <InfoCard label="Yayinlanma" value={formatDate(kb.published_at)} />
        <InfoCard label="Olusturulma" value={formatDate(kb.created_at)} />
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
          <p className="text-2xl font-bold text-violet-700">{kb.source_document_count}</p>
          <p className="text-xs text-gray-500 mt-1">Kaynak Belge</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
          <p className="text-2xl font-bold text-blue-700">{kb.entity_type_count}</p>
          <p className="text-xs text-gray-500 mt-1">Entity Tipi</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
          <p className="text-2xl font-bold text-amber-700">{kb.relationship_type_count}</p>
          <p className="text-xs text-gray-500 mt-1">Iliski Tipi</p>
        </div>
      </div>

      {/* Neo4j Connection */}
      {kb.neo4j_uri && (
        <div className="bg-white rounded-xl border border-gray-200 p-4 mb-6">
          <h3 className="text-sm font-semibold text-gray-700 mb-2">Neo4j Baglantisi</h3>
          <div className="flex flex-wrap gap-x-8 gap-y-1 text-sm">
            <div>
              <span className="text-gray-400">URI: </span>
              <code className="text-gray-700">{kb.neo4j_uri}</code>
            </div>
            <div>
              <span className="text-gray-400">Database: </span>
              <code className="text-gray-700">{kb.neo4j_database}</code>
            </div>
          </div>
        </div>
      )}

      {/* Source Documents */}
      <div className="mb-6">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">
          Kaynak Belgeler ({kb.source_documents.length})
        </h3>
        {kb.source_documents.length === 0 ? (
          <div className="bg-white rounded-xl border border-gray-200 p-8 text-center text-sm text-gray-400">
            Bu KB icin kaynak belge kaydedilmemis.
            <br />
            <span className="text-xs">Workflow yeniden calistirildiginda belgeler burada gorunecek.</span>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50/80">
                  <th className="text-left px-5 py-3 text-xs font-medium text-slate-500">Dosya</th>
                  <th className="text-left px-3 py-3 text-xs font-medium text-slate-500">Tur</th>
                  <th className="text-right px-3 py-3 text-xs font-medium text-slate-500">Boyut</th>
                  <th className="text-left px-5 py-3 text-xs font-medium text-slate-500 hidden lg:table-cell">Resource ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {kb.source_documents.map((doc: SourceDocument, i: number) => (
                  <tr key={doc.resource_id || i} className="hover:bg-blue-50/30 transition-colors">
                    <td className="px-5 py-3">
                      <span className="font-medium text-slate-800 block truncate max-w-[350px]">{doc.filename}</span>
                    </td>
                    <td className="px-3 py-3">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-600">
                        {doc.content_type ? doc.content_type.split('/').pop() : '-'}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right text-slate-600 tabular-nums">
                      {doc.size > 0 ? formatBytes(doc.size) : '-'}
                    </td>
                    <td className="px-5 py-3 hidden lg:table-cell">
                      <span className="text-xs text-slate-400 font-mono">{doc.resource_id || '-'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Ontology */}
      {(entityTypes.length > 0 || relTypes.length > 0) && (
        <div className="mb-6">
          <button
            onClick={() => setShowOntology(!showOntology)}
            className="flex items-center gap-2 text-sm font-semibold text-gray-700 mb-3 hover:text-blue-600 transition-colors"
          >
            <svg
              className={`w-4 h-4 transition-transform ${showOntology ? 'rotate-90' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            Ontology Semasi ({entityTypes.length} entity, {relTypes.length} iliski)
          </button>
          {showOntology && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
              {entityTypes.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-gray-500 mb-2">Entity Tipleri</h4>
                  <div className="flex flex-wrap gap-2">
                    {entityTypes.map((et: any, i: number) => (
                      <span
                        key={i}
                        className="inline-flex items-center px-3 py-1 rounded-lg text-xs font-medium bg-blue-50 text-blue-700"
                      >
                        {typeof et === 'string' ? et : et.name || et.label || JSON.stringify(et)}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {relTypes.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-gray-500 mb-2">Iliski Tipleri</h4>
                  <div className="flex flex-wrap gap-2">
                    {relTypes.map((rt: any, i: number) => (
                      <span
                        key={i}
                        className="inline-flex items-center px-3 py-1 rounded-lg text-xs font-medium bg-amber-50 text-amber-700"
                      >
                        {typeof rt === 'string' ? rt : rt.name || rt.label || JSON.stringify(rt)}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {kb.schema_summary && (
                <div>
                  <h4 className="text-xs font-medium text-gray-500 mb-2">Sema Ozeti</h4>
                  <pre className="text-xs bg-slate-50 rounded-lg p-3 overflow-x-auto max-h-48 border border-slate-200 whitespace-pre-wrap">
                    {kb.schema_summary}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InfoCard({
  label,
  value,
  sub,
  link,
  onClick,
}: {
  label: string;
  value: string;
  sub?: string;
  link?: boolean;
  onClick?: () => void;
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p
        className={`text-sm font-medium ${link ? 'text-blue-600 cursor-pointer hover:underline' : 'text-gray-800'}`}
        onClick={onClick}
      >
        {value}
        {sub && <span className="ml-1 text-xs text-gray-400">{sub}</span>}
      </p>
    </div>
  );
}
