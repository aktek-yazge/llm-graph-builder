import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { workspaceApi, WorkspaceSummary } from '../services/workspaceApi';
import Breadcrumb from '../components/Breadcrumb';

const TENANT_ID = 'default-tenant';

const statusLabels: Record<string, string> = {
  created: 'Olusturuldu',
  sampling: 'Ornek Analiz',
  schema_review: 'Schema Inceleme',
  ready: 'Hazir',
  processing: 'Isleniyor',
  quality_check: 'Kalite Kontrol',
  completed: 'Tamamlandi',
  failed: 'Basarisiz',
};

const statusColors: Record<string, string> = {
  created: 'bg-gray-100 text-gray-800',
  sampling: 'bg-blue-100 text-blue-800',
  schema_review: 'bg-yellow-100 text-yellow-800',
  ready: 'bg-green-100 text-green-800',
  processing: 'bg-indigo-100 text-indigo-800',
  quality_check: 'bg-orange-100 text-orange-800',
  completed: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-red-100 text-red-800',
};

export default function WorkspaceList() {
  const navigate = useNavigate();
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    loadWorkspaces();
  }, []);

  async function loadWorkspaces() {
    try {
      const data = await workspaceApi.listWorkspaces(TENANT_ID);
      setWorkspaces(data);
    } catch (err) {
      console.error('Failed to load workspaces:', err);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const ws = await workspaceApi.createWorkspace({
        name: newName.trim(),
        description: newDesc.trim(),
        tenant_id: TENANT_ID,
      });
      navigate(`/workspaces/${ws.id}`);
    } catch (err) {
      console.error('Failed to create workspace:', err);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto p-6">
      <Breadcrumb items={[
        { label: 'Dashboard', to: '/dashboard' },
        { label: 'Workspaces' },
      ]} />
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Document Workspaces</h1>
          <p className="text-gray-500 mt-1">Belge koleksiyonlarinizi isleyin ve Knowledge Base'e aktarin</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
        >
          + Yeni Workspace
        </button>
      </div>

      {showCreate && (
        <div className="bg-white border border-gray-200 rounded-xl p-6 mb-6 shadow-sm">
          <h3 className="text-lg font-semibold mb-4">Yeni Workspace Olustur</h3>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Workspace Adi</label>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="ornek: Sigorta Policeleri 2024"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Aciklama (opsiyonel)</label>
              <textarea
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="Bu workspace ne tur belgeleri isleyecek?"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                rows={2}
              />
            </div>
            <div className="flex gap-3">
              <button
                onClick={handleCreate}
                disabled={creating || !newName.trim()}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {creating ? 'Olusturuluyor...' : 'Olustur'}
              </button>
              <button
                onClick={() => setShowCreate(false)}
                className="px-4 py-2 text-gray-600 hover:text-gray-800 transition-colors"
              >
                Iptal
              </button>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-center py-12 text-gray-500">Yukleniyor...</div>
      ) : workspaces.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-xl border border-gray-200">
          <div className="text-5xl mb-4">📂</div>
          <h3 className="text-lg font-semibold text-gray-700 mb-2">Henuz workspace yok</h3>
          <p className="text-gray-500 mb-6">Belgelerinizi islemek icin yeni bir workspace olusturun</p>
          <button
            onClick={() => setShowCreate(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            + Yeni Workspace
          </button>
        </div>
      ) : (
        <div className="grid gap-4">
          {workspaces.map((ws) => (
            <div
              key={ws.id}
              onClick={() => navigate(`/workspaces/${ws.id}`)}
              className="bg-white border border-gray-200 rounded-xl p-5 hover:shadow-md cursor-pointer transition-shadow"
            >
              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-1">
                    <h3 className="text-lg font-semibold text-gray-900">{ws.name}</h3>
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColors[ws.status] || 'bg-gray-100 text-gray-800'}`}>
                      {statusLabels[ws.status] || ws.status}
                    </span>
                  </div>
                  <div className="flex gap-6 text-sm text-gray-500 mt-2">
                    <span>{ws.document_count} belge</span>
                    <span>{ws.processed_count} islenmis</span>
                    {ws.success_rate > 0 && <span>%{ws.success_rate} basari</span>}
                  </div>
                </div>
                <span className="text-gray-400">&rarr;</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
