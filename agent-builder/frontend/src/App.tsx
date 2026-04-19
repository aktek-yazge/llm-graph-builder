import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { type ReactNode } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import Dashboard from './pages/Dashboard';
import BuilderChat from './pages/BuilderChat';
import ChatAgentList from './pages/ChatAgentList';
import ChatAgentDetailPage from './pages/ChatAgentDetail';
import EvolvingAgentPage from './pages/EvolvingAgentPage';
import WikiPage from './pages/WikiPage';
import WorkflowPage from './pages/WorkflowPage';
import WorkflowsPage from './pages/WorkflowsPage';
import ResourcesPage from './pages/ResourcesPage';
import KnowledgesPage from './pages/KnowledgesPage';
import KnowledgeDetailPage from './pages/KnowledgeDetailPage';
import EvaluationsPage from './pages/EvaluationsPage';
import GuardrailsPage from './pages/GuardrailsPage';
import ToolsPage from './pages/ToolsPage';
import ModelsPage from './pages/ModelsPage';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import NavBar from './components/NavBar';

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-2 border-blue-200 border-t-blue-600" />
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <>{children}</>;
}

function AppRoutes() {
  const { user, loading } = useAuth();
  const isAuth = !!user;
  const showNav = isAuth && !loading;

  return (
    <div className="h-screen flex flex-col bg-white">
      {showNav && <NavBar />}
      <div className="flex-1 min-h-0 overflow-auto">
        <Routes>
          {/* Public routes */}
          <Route path="/login" element={isAuth ? <Navigate to="/dashboard" replace /> : <LoginPage />} />
          <Route path="/register" element={isAuth ? <Navigate to="/dashboard" replace /> : <RegisterPage />} />

          {/* Protected routes */}
          <Route path="/" element={<ProtectedRoute><Navigate to="/dashboard" replace /></ProtectedRoute>} />
          <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />

          <Route path="/agents" element={<ProtectedRoute><EvolvingAgentPage /></ProtectedRoute>} />
          <Route path="/agents/:agentId" element={<ProtectedRoute><EvolvingAgentPage /></ProtectedRoute>} />
          <Route path="/agents/:agentId/workflow" element={<ProtectedRoute><WorkflowPage /></ProtectedRoute>} />
          <Route path="/agents/:agentId/wiki" element={<ProtectedRoute><WikiPage /></ProtectedRoute>} />
          <Route path="/agents/:agentId/wiki/*" element={<ProtectedRoute><WikiPage /></ProtectedRoute>} />

          <Route path="/workflows" element={<ProtectedRoute><WorkflowsPage /></ProtectedRoute>} />
          <Route path="/resources" element={<ProtectedRoute><ResourcesPage /></ProtectedRoute>} />
          <Route path="/knowledges" element={<ProtectedRoute><KnowledgesPage /></ProtectedRoute>} />
          <Route path="/knowledges/:endpointId" element={<ProtectedRoute><KnowledgeDetailPage /></ProtectedRoute>} />
          <Route path="/evaluations" element={<ProtectedRoute><EvaluationsPage /></ProtectedRoute>} />
          <Route path="/guardrails" element={<ProtectedRoute><GuardrailsPage /></ProtectedRoute>} />
          <Route path="/tools" element={<ProtectedRoute><ToolsPage /></ProtectedRoute>} />
          <Route path="/models" element={<ProtectedRoute><ModelsPage /></ProtectedRoute>} />

          <Route path="/builder/:sessionId?" element={<ProtectedRoute><BuilderChat /></ProtectedRoute>} />
          <Route path="/admin/agents" element={<ProtectedRoute><ChatAgentList /></ProtectedRoute>} />
          <Route path="/admin/agents/:agentId" element={<ProtectedRoute><ChatAgentDetailPage /></ProtectedRoute>} />

          {/* Backward-compat redirects */}
          <Route path="/evolving" element={<Navigate to="/agents" replace />} />
          <Route path="/evolving/:agentId" element={<Navigate to="/agents" replace />} />
          <Route path="/evolving/:agentId/workflow" element={<Navigate to="/agents" replace />} />
          <Route path="/evolving/:agentId/ecosystem" element={<Navigate to="/agents" replace />} />
          <Route path="/chat" element={<Navigate to="/agents" replace />} />
          <Route path="/chat-agents" element={<Navigate to="/admin/agents" replace />} />
          <Route path="/chat-agents/:agentId" element={<Navigate to="/admin/agents" replace />} />
          <Route path="/agents-list" element={<Navigate to="/admin/agents" replace />} />
        </Routes>
      </div>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
