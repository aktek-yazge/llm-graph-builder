import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AgentBuilderAPI } from '../services/agentBuilderApi';

interface Agent {
  id: string;
  name: string;
  description: string;
  status: 'draft' | 'active' | 'archived';
  skill_count: number;
  deployed: boolean;
}

interface Session {
  id: string;
  name: string;
  state: string;
  created_at: string;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [tenantId] = useState('default');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [agentsRes, sessionsRes] = await Promise.all([
        AgentBuilderAPI.listAgents(tenantId),
        AgentBuilderAPI.listSessions(tenantId),
      ]);
      setAgents(agentsRes || []);
      setSessions(sessionsRes || []);
    } catch (error) {
      console.error('Failed to load data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleNewSession = async () => {
    try {
      const session = await AgentBuilderAPI.createSession(tenantId);
      navigate(`/builder/${session.id}`);
    } catch (error) {
      console.error('Failed to create session:', error);
    }
  };

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

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Agent Builder</h1>
          <p className="mt-2 text-gray-600">
            Goal-driven yaklaşımla özel agent'lar oluşturun
          </p>
        </div>
        <button
          onClick={handleNewSession}
          className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-primary-600 hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500"
        >
          <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Yeni Agent Oluştur
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Active Agents */}
        <div className="bg-white rounded-lg shadow">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Agent'larım</h2>
          </div>
          <div className="divide-y divide-gray-200">
            {agents.length === 0 ? (
              <div className="px-6 py-8 text-center text-gray-500">
                Henüz agent oluşturmadınız
              </div>
            ) : (
              agents.map((agent) => (
                <Link
                  key={agent.id}
                  to={`/agents/${agent.id}`}
                  className="block px-6 py-4 hover:bg-gray-50 transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-sm font-medium text-gray-900">{agent.name}</h3>
                      <p className="text-sm text-gray-500 mt-1">{agent.description}</p>
                    </div>
                    <div className="flex items-center space-x-3">
                      <span className="text-xs text-gray-500">
                        {agent.skill_count} skill
                      </span>
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusColor(agent.status)}`}>
                        {agent.status}
                      </span>
                      {agent.deployed && (
                        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                          Deployed
                        </span>
                      )}
                    </div>
                  </div>
                </Link>
              ))
            )}
          </div>
        </div>

        {/* Recent Sessions */}
        <div className="bg-white rounded-lg shadow">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Son Oturumlar</h2>
          </div>
          <div className="divide-y divide-gray-200">
            {sessions.length === 0 ? (
              <div className="px-6 py-8 text-center text-gray-500">
                Henüz oturum yok
              </div>
            ) : (
              sessions.slice(0, 5).map((session) => (
                <Link
                  key={session.id}
                  to={`/builder/${session.id}`}
                  className="block px-6 py-4 hover:bg-gray-50 transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-sm font-medium text-gray-900">
                        {session.name || `Oturum ${session.id.slice(-8)}`}
                      </h3>
                      <p className="text-xs text-gray-500 mt-1">
                        {new Date(session.created_at).toLocaleString('tr-TR')}
                      </p>
                    </div>
                    <span className="text-xs text-gray-400">{session.state}</span>
                  </div>
                </Link>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center">
            <div className="flex-shrink-0 bg-primary-100 rounded-md p-3">
              <svg className="h-6 w-6 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <div className="ml-4">
              <h3 className="text-sm font-medium text-gray-900">Belge İşle</h3>
              <p className="text-xs text-gray-500">Mevcut agent ile belge işle</p>
            </div>
          </div>
        </div>

        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center">
            <div className="flex-shrink-0 bg-green-100 rounded-md p-3">
              <svg className="h-6 w-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
              </svg>
            </div>
            <div className="ml-4">
              <h3 className="text-sm font-medium text-gray-900">Skill Kütüphanesi</h3>
              <p className="text-xs text-gray-500">Mevcut skill'leri keşfet</p>
            </div>
          </div>
        </div>

        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center">
            <div className="flex-shrink-0 bg-purple-100 rounded-md p-3">
              <svg className="h-6 w-6 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
            <div className="ml-4">
              <h3 className="text-sm font-medium text-gray-900">Ontology Explorer</h3>
              <p className="text-xs text-gray-500">Entity ve ilişkileri görüntüle</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
