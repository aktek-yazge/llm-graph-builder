import { useState, useRef, useEffect } from 'react';
import { RichMessagePart } from '../services/agentBuilderApi';
import RichMessage from './RichMessage';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  richParts?: RichMessagePart[];
}

interface ChatPanelProps {
  messages: ChatMessage[];
  onSend: (message: string) => Promise<void>;
  onAction?: (action: string) => void;
  onUploadFiles?: (files: File[]) => void;
  onTriggerSideUpload?: () => void;
  title?: string;
  placeholder?: string;
  loading?: boolean;
  disabled?: boolean;
}

export default function ChatPanel({
  messages,
  onSend,
  onAction,
  onUploadFiles,
  onTriggerSideUpload,
  title = 'Chat',
  placeholder = 'Mesajinizi yazin...',
  loading = false,
  disabled = false,
}: ChatPanelProps) {
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const msg = input.trim();
    if (!msg || loading || disabled) return;
    setInput('');
    await onSend(msg);
    inputRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  }

  function handleSuggestion(text: string) {
    onSend(text);
  }

  function handleAction(action: string) {
    if (onAction) {
      onAction(action);
    } else {
      onSend(action);
    }
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 border border-gray-200 rounded-xl bg-white shadow-sm">
      {title && (
        <div className="px-4 py-3 border-b border-gray-100 bg-gray-50 rounded-t-xl">
          <h3 className="text-sm font-semibold text-gray-700">{title}</h3>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 && !loading && (
          <div className="text-center text-gray-400 text-sm py-8">
            Henuz mesaj yok. Sohbete baslayin.
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white rounded-br-md'
                  : 'bg-gray-100 text-gray-800 rounded-bl-md'
              }`}
            >
              {msg.richParts && msg.richParts.length > 0 ? (
                <RichMessage
                  parts={msg.richParts}
                  onAction={handleAction}
                  onSuggestion={handleSuggestion}
                  onUploadFiles={onUploadFiles}
                  onTriggerSideUpload={onTriggerSideUpload}
                />
              ) : (
                <span className="whitespace-pre-wrap">{msg.content}</span>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-100 rounded-2xl rounded-bl-md px-4 py-3">
              <div className="flex space-x-1.5">
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSubmit} className="border-t border-gray-100 p-3 flex gap-2">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={loading || disabled}
          rows={1}
          className="flex-1 resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                     disabled:bg-gray-50 disabled:text-gray-400"
        />
        <button
          type="submit"
          disabled={!input.trim() || loading || disabled}
          className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium
                     hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed
                     transition-colors"
        >
          Gonder
        </button>
      </form>
    </div>
  );
}
