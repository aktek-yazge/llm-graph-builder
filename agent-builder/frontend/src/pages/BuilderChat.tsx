import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { AgentBuilderAPI } from '../services/agentBuilderApi';

interface Message {
  role: 'user' | 'assistant';
  content: string;
  options?: string[];
}

interface Session {
  id: string;
  state: string;
  context: Record<string, unknown>;
}

export default function BuilderChat() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [tenantId] = useState('default');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (sessionId) {
      loadSession(sessionId);
    } else {
      createNewSession();
    }
  }, [sessionId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const createNewSession = async () => {
    try {
      const newSession = await AgentBuilderAPI.createSession(tenantId);
      navigate(`/builder/${newSession.id}`, { replace: true });
      setSession(newSession);
      addMessage('assistant', 'Merhaba! Hangi tür belgelerle çalışacak bir agent oluşturmak istiyorsunuz?');
    } catch (error) {
      console.error('Failed to create session:', error);
    }
  };

  const loadSession = async (id: string) => {
    try {
      const sessionData = await AgentBuilderAPI.getSession(id);
      setSession(sessionData);
      if (sessionData.messages) {
        setMessages(sessionData.messages);
      } else {
        addMessage('assistant', 'Oturum yüklendi. Devam etmek için bir mesaj yazın.');
      }
    } catch (error) {
      console.error('Failed to load session:', error);
      createNewSession();
    }
  };

  const addMessage = (role: 'user' | 'assistant', content: string, options?: string[]) => {
    setMessages((prev) => [...prev, { role, content, options }]);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !session) return;

    const userMessage = input.trim();
    setInput('');
    addMessage('user', userMessage);
    setLoading(true);

    try {
      const response = await AgentBuilderAPI.sendMessage(session.id, userMessage);
      
      if (response.message) {
        addMessage('assistant', response.message, response.options);
      }
      
      if (response.state) {
        setSession((prev) => prev ? { ...prev, state: response.state } : null);
      }
    } catch (error) {
      console.error('Failed to send message:', error);
      addMessage('assistant', 'Bir hata oluştu. Lütfen tekrar deneyin.');
    } finally {
      setLoading(false);
    }
  };

  const handleOptionClick = (option: string) => {
    setInput(option);
  };

  return (
    <div className="flex h-screen bg-gray-100">
      {/* Sidebar */}
      <div className="w-64 bg-white border-r border-gray-200 flex flex-col">
        <div className="p-4 border-b border-gray-200">
          <button
            onClick={() => navigate('/dashboard')}
            className="flex items-center text-gray-600 hover:text-gray-900"
          >
            <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Dashboard
          </button>
        </div>
        
        <div className="p-4 flex-1">
          <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-3">
            Oturum Durumu
          </h3>
          {session && (
            <div className="space-y-2">
              <div className="text-sm">
                <span className="text-gray-500">State:</span>
                <span className="ml-2 font-medium text-gray-900">{session.state}</span>
              </div>
              <div className="text-sm">
                <span className="text-gray-500">ID:</span>
                <span className="ml-2 font-mono text-xs text-gray-600">{session.id.slice(-8)}</span>
              </div>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-gray-200">
          <button
            onClick={createNewSession}
            className="w-full flex items-center justify-center px-4 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
          >
            <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            Yeni Oturum
          </button>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <div className="bg-white border-b border-gray-200 px-6 py-4">
          <h1 className="text-lg font-semibold text-gray-900">Agent Builder</h1>
          <p className="text-sm text-gray-500">Goal-driven agent oluşturma</p>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {messages.map((message, index) => (
            <div
              key={index}
              className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-2xl px-4 py-3 rounded-lg ${
                  message.role === 'user'
                    ? 'bg-primary-600 text-white'
                    : 'bg-white shadow-sm border border-gray-200'
                }`}
              >
                <p className="text-sm whitespace-pre-wrap">{message.content}</p>
                {message.options && message.options.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {message.options.map((option, i) => (
                      <button
                        key={i}
                        onClick={() => handleOptionClick(option)}
                        className="px-3 py-1 text-xs font-medium text-primary-600 bg-primary-50 rounded-full hover:bg-primary-100 transition-colors"
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-white shadow-sm border border-gray-200 px-4 py-3 rounded-lg">
                <div className="flex space-x-2">
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></div>
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></div>
                  <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="bg-white border-t border-gray-200 p-4">
          <form onSubmit={handleSubmit} className="flex space-x-4">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Mesajınızı yazın..."
              className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="px-6 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Gönder
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
