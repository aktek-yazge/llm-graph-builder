import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import BuilderChat from './pages/BuilderChat';
import AgentDetail from './pages/AgentDetail';

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/builder/:sessionId?" element={<BuilderChat />} />
          <Route path="/agents/:agentId" element={<AgentDetail />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}

export default App;
