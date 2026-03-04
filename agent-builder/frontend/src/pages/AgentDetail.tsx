import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { AgentBuilderAPI } from '../services/agentBuilderApi';
import Breadcrumb from '../components/Breadcrumb';

interface Skill {
  id: string;
  name: string;
  description: string;
  skill_category: string;
  effectiveness_score: number;
}

interface Agent {
  id: string;
  name: string;
  description: string;
  purpose: string;
  status: 'draft' | 'active' | 'archived';
  tenant_id: string;
  mcp_virtual_server_id: string | null;
  created_at: string;
  skills: Skill[];
}

export default function AgentDetail() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [deploying, setDeploying] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [fileIds, setFileIds] = useState('');

  useEffect(() => {
    if (agentId) {
      loadAgent(agentId);
    }
  }, [agentId]);

  const loadAgent = async (id: string) => {
    try {
      setLoading(true);
      const data = await AgentBuilderAPI.getAgent(id);
      setAgent(data);
    } catch (error) {
      console.error('Failed to load agent:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleDeploy = async () => {
    if (!agentId) return;
    
    try {
      setDeploying(true);
      await AgentBuilderAPI.deployAgent(agentId);
      await loadAgent(agentId);
    } catch (error) {
      console.error('Failed to deploy agent:', error);
    } finally {
      setDeploying(false);
    }
  };

  const handleProcess = async () => {
    if (!agentId || !fileIds.trim()) return;
    
    try {
      setProcessing(true);
      const ids = fileIds.split(',').map((id) => id.trim());
      const result = await AgentBuilderAPI.processWithAgent(agentId, ids);
      alert(`İşlem başlatıldı. Task ID: ${result.task_id}`);
      setFileIds('');
    } catch (error) {
      console.error('Failed to process:', error);
      alert('İşlem başlatılamadı.');
    } finally {
      setProcessing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <h2 className="text-xl font-semibold text-gray-900">Agent bulunamadı</h2>
          <button
            onClick={() => navigate('/dashboard')}
            className="mt-4 text-primary-600 hover:text-primary-700"
          >
            Dashboard'a dön
          </button>
        </div>
      </div>
    );
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'active':
        return 'bg-green-100 text-green-800';
      case 'draft':
        return 'bg-yellow-100 text-yellow-800';
      case 'archived':
        return 'bg-gray-100 text-gray-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <Breadcrumb items={[
        { label: 'Dashboard', to: '/dashboard' },
        { label: 'Agents', to: '/dashboard' },
        { label: agent.name },
      ]} />

      {/* Header */}
      <div className="mb-8">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-3xl font-bold text-gray-900">{agent.name}</h1>
            <p className="mt-2 text-gray-600">{agent.description}</p>
          </div>
          <span className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${getStatusColor(agent.status)}`}>
            {agent.status}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Main Content */}
        <div className="lg:col-span-2 space-y-6">
          {/* Info Card */}
          <div className="bg-white rounded-lg shadow p-6">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">Detaylar</h2>
            <dl className="grid grid-cols-2 gap-4">
              <div>
                <dt className="text-sm text-gray-500">Purpose</dt>
                <dd className="text-sm font-medium text-gray-900">{agent.purpose || '-'}</dd>
              </div>
              <div>
                <dt className="text-sm text-gray-500">Tenant ID</dt>
                <dd className="text-sm font-mono text-gray-900">{agent.tenant_id}</dd>
              </div>
              <div>
                <dt className="text-sm text-gray-500">Oluşturulma</dt>
                <dd className="text-sm text-gray-900">
                  {new Date(agent.created_at).toLocaleString('tr-TR')}
                </dd>
              </div>
              <div>
                <dt className="text-sm text-gray-500">Virtual Server ID</dt>
                <dd className="text-sm font-mono text-gray-900">
                  {agent.mcp_virtual_server_id || '-'}
                </dd>
              </div>
            </dl>
          </div>

          {/* Skills */}
          <div className="bg-white rounded-lg shadow p-6">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">
              Skill'ler ({agent.skills?.length || 0})
            </h2>
            {agent.skills && agent.skills.length > 0 ? (
              <div className="space-y-3">
                {agent.skills.map((skill) => (
                  <div
                    key={skill.id}
                    className="border border-gray-200 rounded-lg p-4 hover:bg-gray-50"
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="text-sm font-medium text-gray-900">{skill.name}</h3>
                        <p className="text-xs text-gray-500 mt-1">{skill.description}</p>
                      </div>
                      <div className="flex items-center space-x-3">
                        <span className="text-xs text-gray-400">{skill.skill_category}</span>
                        <div className="flex items-center">
                          <div className="w-16 bg-gray-200 rounded-full h-2">
                            <div
                              className="bg-green-500 h-2 rounded-full"
                              style={{ width: `${(skill.effectiveness_score || 0.5) * 100}%` }}
                            ></div>
                          </div>
                          <span className="ml-2 text-xs text-gray-500">
                            {Math.round((skill.effectiveness_score || 0.5) * 100)}%
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-500">Henüz skill eklenmemiş</p>
            )}
          </div>
        </div>

        {/* Sidebar Actions */}
        <div className="space-y-6">
          {/* Deploy Card */}
          <div className="bg-white rounded-lg shadow p-6">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">Deployment</h2>
            {agent.status === 'active' ? (
              <div className="text-center">
                <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-green-100 mb-3">
                  <svg className="w-6 h-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <p className="text-sm text-green-600 font-medium">Agent aktif</p>
                <p className="text-xs text-gray-500 mt-1">
                  Endpoint: /agents/{agent.id}/process
                </p>
              </div>
            ) : (
              <button
                onClick={handleDeploy}
                disabled={deploying}
                className="w-full px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-500 disabled:opacity-50"
              >
                {deploying ? 'Deploy ediliyor...' : 'Deploy Et'}
              </button>
            )}
          </div>

          {/* Process Card */}
          {agent.status === 'active' && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold text-gray-900 mb-4">Belge İşle</h2>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm text-gray-600 mb-1">
                    Dosya ID'leri (virgülle ayırın)
                  </label>
                  <input
                    type="text"
                    value={fileIds}
                    onChange={(e) => setFileIds(e.target.value)}
                    placeholder="1, 2, 3"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 text-sm"
                  />
                </div>
                <button
                  onClick={handleProcess}
                  disabled={processing || !fileIds.trim()}
                  className="w-full px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-green-500 disabled:opacity-50"
                >
                  {processing ? 'İşleniyor...' : 'İşle'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
