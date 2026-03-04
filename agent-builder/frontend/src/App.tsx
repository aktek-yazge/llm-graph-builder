import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import BuilderChat from './pages/BuilderChat';
import AgentDetail from './pages/AgentDetail';
import WorkspaceList from './pages/WorkspaceList';
import WorkspaceDetail from './pages/WorkspaceDetail';
import ResourceList from './pages/ResourceList';
import ChatAgentList from './pages/ChatAgentList';
import ChatAgentDetailPage from './pages/ChatAgentDetail';

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/builder/:sessionId?" element={<BuilderChat />} />
          <Route path="/agents/:agentId" element={<AgentDetail />} />
          <Route path="/workspaces" element={<WorkspaceList />} />
          <Route path="/workspaces/:workspaceId" element={<WorkspaceDetail />} />
          <Route path="/resources" element={<ResourceList />} />
          <Route path="/chat-agents" element={<ChatAgentList />} />
          <Route path="/chat-agents/:agentId" element={<ChatAgentDetailPage />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}

export default App;
