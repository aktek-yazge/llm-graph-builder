import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import BuilderChat from './pages/BuilderChat';
import AgentDetail from './pages/AgentDetail';
import WorkspaceList from './pages/WorkspaceList';
import WorkspaceDetail from './pages/WorkspaceDetail';
import ResourceList from './pages/ResourceList';
import ChatAgentList from './pages/ChatAgentList';
import ChatAgentDetailPage from './pages/ChatAgentDetail';
import OrchestratorChat from './pages/OrchestratorChat';
import EvolvingAgentPage from './pages/EvolvingAgentPage';
import EcosystemPage from './pages/EcosystemPage';
import WikiPage from './pages/WikiPage';
import NavBar from './components/NavBar';

function App() {
  return (
    <BrowserRouter>
      <div className="h-screen flex flex-col bg-gray-50">
        <NavBar />
        <div className="flex-1 min-h-0 overflow-auto">
          <Routes>
            {/* Son kullanici */}
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/chat" element={<OrchestratorChat />} />

            {/* Evolving Agent */}
            <Route path="/evolving" element={<EvolvingAgentPage />} />
            <Route path="/evolving/:agentId" element={<EvolvingAgentPage />} />
            <Route path="/evolving/:agentId/ecosystem" element={<EcosystemPage />} />
            <Route path="/evolving/:agentId/wiki" element={<WikiPage />} />
            <Route path="/evolving/:agentId/wiki/*" element={<WikiPage />} />

            {/* Admin / Yonetim */}
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/builder/:sessionId?" element={<BuilderChat />} />
            <Route path="/agents/:agentId" element={<AgentDetail />} />
            <Route path="/workspaces" element={<WorkspaceList />} />
            <Route path="/workspaces/:workspaceId" element={<WorkspaceDetail />} />
            <Route path="/resources" element={<ResourceList />} />

            {/* Admin: Agent yonetimi */}
            <Route path="/admin/agents" element={<ChatAgentList />} />
            <Route path="/admin/agents/:agentId" element={<ChatAgentDetailPage />} />

            {/* Eski route'lardan redirect */}
            <Route path="/chat-agents" element={<Navigate to="/admin/agents" replace />} />
            <Route path="/chat-agents/:agentId" element={<Navigate to="/admin/agents" replace />} />
            <Route path="/agents-list" element={<Navigate to="/admin/agents" replace />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  );
}

export default App;
