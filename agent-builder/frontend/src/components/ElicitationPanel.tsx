import { useEffect, useState } from 'react';
import { workspaceApi, ElicitationItem } from '../services/workspaceApi';

interface ElicitationPanelProps {
  workspaceId: string;
  pendingCount?: number;
}

export default function ElicitationPanel({ workspaceId, pendingCount = 0 }: ElicitationPanelProps) {
  const [items, setItems] = useState<ElicitationItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [resolving, setResolving] = useState<string | null>(null);

  useEffect(() => {
    if (expanded || pendingCount > 0) loadQueue();
  }, [workspaceId, pendingCount, expanded]);

  async function loadQueue() {
    setLoading(true);
    try {
      const data = await workspaceApi.getElicitationQueue(workspaceId, 'pending', 20);
      setItems(data.items || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  async function handleResolve(requestId: string, action: 'accept' | 'reject') {
    setResolving(requestId);
    try {
      await workspaceApi.resolveElicitation(workspaceId, requestId, action);
      setItems((prev) => prev.filter((item) => item.id !== requestId));
    } catch (err) {
      console.error('Elicitation resolve failed:', err);
    } finally {
      setResolving(null);
    }
  }

  if (items.length === 0 && pendingCount === 0) return null;

  return (
    <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between w-full"
      >
        <div className="flex items-center gap-2">
          <span className="text-amber-600 font-bold text-sm">!</span>
          <h3 className="text-sm font-semibold text-amber-800">
            Inceleme Bekliyor
          </h3>
          {(items.length > 0 || pendingCount > 0) && (
            <span className="bg-amber-200 text-amber-800 text-xs font-bold px-2 py-0.5 rounded-full">
              {items.length || pendingCount}
            </span>
          )}
        </div>
        <span className="text-amber-400 text-xs">{expanded ? 'Gizle' : 'Goster'}</span>
      </button>

      {expanded && (
        <div className="mt-3 space-y-2">
          {loading && <p className="text-xs text-amber-600">Yukleniyor...</p>}

          {items.map((item) => (
            <div
              key={item.id}
              className="bg-white rounded-lg border border-amber-100 p-3"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-gray-800 truncate flex-1">
                  {item.file_name || item.doc_id}
                </span>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                  item.confidence_score < 0.5
                    ? 'bg-red-100 text-red-700'
                    : 'bg-yellow-100 text-yellow-700'
                }`}>
                  %{(item.confidence_score * 100).toFixed(0)}
                </span>
              </div>

              <div className="text-xs text-gray-500 mb-2">
                {item.node_count} entity, {item.relationship_count} iliski
              </div>

              <div className="flex gap-2">
                <button
                  onClick={() => handleResolve(item.id, 'accept')}
                  disabled={resolving === item.id}
                  className="flex-1 px-3 py-1.5 text-xs font-medium rounded-lg bg-green-100 text-green-700 hover:bg-green-200 disabled:opacity-50"
                >
                  Onayla
                </button>
                <button
                  onClick={() => handleResolve(item.id, 'reject')}
                  disabled={resolving === item.id}
                  className="flex-1 px-3 py-1.5 text-xs font-medium rounded-lg bg-red-100 text-red-700 hover:bg-red-200 disabled:opacity-50"
                >
                  Reddet
                </button>
              </div>
            </div>
          ))}

          {items.length === 0 && !loading && (
            <p className="text-xs text-amber-600 text-center py-2">Inceleme kuyruğu bos</p>
          )}
        </div>
      )}
    </div>
  );
}
