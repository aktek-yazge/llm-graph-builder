import { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  orchestratorConnect,
  orchestratorChat,
  getOrchestratorStatus,
  OrchestratorStatus,
} from '../services/chatAgentApi';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  experts?: string[];
  timestamp: Date;
}

function parseExpertBadges(content: string): string[] {
  const matches = content.match(/===\s*(.+?)\s*(?:\(Kaynak:.*?\))?\s*===/g);
  if (!matches) return [];
  return matches.map((m) => m.replace(/===/g, '').replace(/\(Kaynak:.*?\)/, '').trim());
}

export default function OrchestratorChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState('');
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(true);
  const [status, setStatus] = useState<OrchestratorStatus | null>(null);
  const [error, setError] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    initSession();
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function initSession() {
    setConnecting(true);
    try {
      const [connectResult, statusResult] = await Promise.allSettled([
        orchestratorConnect(),
        getOrchestratorStatus(),
      ]);

      if (connectResult.status === 'fulfilled') {
        setSessionId(connectResult.value.session_id);
        setConnected(connectResult.value.status === 'connected');
        if (connectResult.value.status === 'no_orchestrator') {
          setError(connectResult.value.message || 'Orkestrator yapilandirilmamis');
        }
      } else {
        setError('Baglanma hatasi. Sunucu calisiyor mu?');
      }

      if (statusResult.status === 'fulfilled') {
        setStatus(statusResult.value);
      }
    } catch (err) {
      setError(`Baglanti hatasi: ${err}`);
    } finally {
      setConnecting(false);
    }
  }

  const handleSend = useCallback(async () => {
    if (!input.trim() || loading) return;

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: input.trim(),
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setLoading(true);
    setError('');

    let assistantContent = '';
    const assistantId = `a-${Date.now()}`;

    setMessages((prev) => [
      ...prev,
      { id: assistantId, role: 'assistant', content: '', timestamp: new Date() },
    ]);

    try {
      abortRef.current = await orchestratorChat(
        userMsg.content,
        sessionId,
        (data) => {
          try {
            const parsed = JSON.parse(data);
            if (parsed.type === 'message_chunk' && parsed.content) {
              assistantContent += parsed.content;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: assistantContent, experts: parseExpertBadges(assistantContent) }
                    : m,
                ),
              );
            } else if (parsed.type === 'final_response' && parsed.content) {
              assistantContent = parsed.content;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: assistantContent, experts: parseExpertBadges(assistantContent) }
                    : m,
                ),
              );
            } else if (parsed.type === 'error') {
              setError(parsed.message || 'Bilinmeyen hata');
            }
          } catch {
            // non-JSON chunk
          }
        },
        () => setLoading(false),
        (err) => {
          setError(err.message);
          setLoading(false);
        },
      );
    } catch (err) {
      setError(`Gonderim hatasi: ${err}`);
      setLoading(false);
    }
  }, [input, loading, sessionId]);

  if (connecting) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-slate-50">
        <div className="flex flex-col items-center gap-3">
          <div className="animate-spin rounded-full h-10 w-10 border-2 border-indigo-200 border-t-indigo-600" />
          <span className="text-sm text-slate-400">Baglaniliyor...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-slate-50">
      {/* Header */}
      <div className="bg-white border-b border-slate-200 px-4 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <Link to="/dashboard" className="text-slate-400 hover:text-slate-600 transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </Link>
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-sm font-bold">
            O
          </div>
          <div>
            <h1 className="text-sm font-semibold text-slate-900">Soru Sor</h1>
            <p className="text-xs text-slate-500">
              {status?.experts.total
                ? `${status.experts.total} uzman agent aktif`
                : 'Uzman agentlar sorgulaniyor...'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {status?.experts.names.map((name) => (
            <span
              key={name}
              className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-100"
            >
              {name}
            </span>
          ))}
          <Link
            to="/admin/agents"
            className="text-xs text-slate-400 hover:text-slate-600 transition-colors ml-2"
          >
            Yonetim
          </Link>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.length === 0 && !error && (
            <div className="text-center py-20">
              <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-2xl font-bold mx-auto mb-4">
                ?
              </div>
              <h2 className="text-xl font-semibold text-slate-900 mb-2">Merhaba!</h2>
              <p className="text-sm text-slate-500 max-w-md mx-auto">
                Sorunuzu yazmaya baslayin. Ilgili uzman agentlar otomatik olarak
                sorgulanacak ve size sentezlenmis bir yanit sunulacak.
              </p>
              {status && status.experts.total > 0 && (
                <div className="mt-6 flex flex-wrap justify-center gap-2">
                  {status.experts.names.map((name) => (
                    <span
                      key={name}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium bg-white border border-slate-200 text-slate-600"
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                      {name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
              {error}
            </div>
          )}

          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-3 ${
                  msg.role === 'user'
                    ? 'bg-indigo-600 text-white'
                    : 'bg-white border border-slate-200 text-slate-900'
                }`}
              >
                {msg.role === 'assistant' && msg.experts && msg.experts.length > 0 && (
                  <div className="flex flex-wrap gap-1 mb-2">
                    {msg.experts.map((expert) => (
                      <span
                        key={expert}
                        className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-100"
                      >
                        {expert}
                      </span>
                    ))}
                  </div>
                )}
                <div className="text-sm whitespace-pre-wrap leading-relaxed">
                  {msg.content || (msg.role === 'assistant' && loading ? (
                    <span className="inline-flex items-center gap-1 text-slate-400">
                      <span className="animate-pulse">Dusunuyor</span>
                      <span className="animate-bounce">...</span>
                    </span>
                  ) : null)}
                </div>
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="bg-white border-t border-slate-200 px-4 py-3 shrink-0">
        <div className="max-w-3xl mx-auto">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
            className="flex gap-2"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Sorunuzu yazmaya baslayin..."
              disabled={loading || !connected}
              className="flex-1 px-4 py-3 border border-slate-200 rounded-xl text-sm focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none disabled:bg-slate-50 disabled:text-slate-400"
            />
            <button
              type="submit"
              disabled={loading || !input.trim() || !connected}
              className="px-6 py-3 bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 disabled:opacity-50 text-sm font-medium transition-colors flex items-center gap-2"
            >
              {loading ? (
                <div className="animate-spin rounded-full h-4 w-4 border-2 border-white/30 border-t-white" />
              ) : (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                </svg>
              )}
              Gonder
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
