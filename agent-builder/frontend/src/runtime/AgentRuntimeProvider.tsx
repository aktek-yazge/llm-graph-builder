import { useCallback, useMemo, useRef, type ReactNode } from 'react';
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type ThreadMessageLike,
  type AppendMessage,
} from '@assistant-ui/react';
import { useAgentContext, type ChatMessage } from '../context/AgentContext';

// Tool'lar sag paneldeki Gorevler bolumunde zaten gosteriliyor;
// Assistant-UI chat thread'inde goruntulenmez.
// get_current_plan: sag plan panelinde zaten canli olarak goruluyor.
// Sohbete her turn'de yeniden basilmasi gereksiz.
const HIDDEN_TOOLS = new Set(['write_todos', 'read_todos', 'get_current_plan']);

// ThreadMessageLike.tool-call.args bekliyor: ReadonlyJSONObject. Assistant-UI
// tool-call part'ini olusturmak icin herhangi bir JSON-serializable obje yeterli.
type JsonArgs = Record<string, unknown>;

function parseMaybeJSON(text: string | undefined): JsonArgs | undefined {
  if (!text) return undefined;
  const trimmed = text.trim();
  if (!(trimmed.startsWith('{') || trimmed.startsWith('['))) return undefined;
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as JsonArgs;
    }
  } catch {
    return undefined;
  }
  return undefined;
}

function convertMessage(m: ChatMessage): ThreadMessageLike {
  if (m.role === 'user') {
    return {
      id: m.id,
      role: 'user',
      content: [{ type: 'text', text: m.content || '' }],
      createdAt: new Date(m.timestamp),
    };
  }

  if (m.role === 'tool') {
    const toolName = m.toolName || 'tool';
    const args = m.toolInput ?? parseMaybeJSON(m.content) ?? {};
    const result = m.content ? m.content : undefined;
    return {
      id: m.id,
      role: 'assistant',
      createdAt: new Date(m.timestamp),
      content: [
        {
          type: 'tool-call',
          toolCallId: m.id,
          toolName,
          // ThreadMessageLike.args tipi ReadonlyJSONObject ama ChatMessage.toolInput
          // runtime tarafinda opak bir JSON olarak saklandigi icin narrow tipe
          // kasitli olarak cast ediyoruz.
          args: args as unknown as Record<string, never>,
          result,
        },
      ],
    };
  }

  return {
    id: m.id,
    role: 'assistant',
    content: [{ type: 'text', text: m.content || '' }],
    createdAt: new Date(m.timestamp),
  };
}

export function AgentRuntimeProvider({ children }: { children: ReactNode }) {
  const { messages, sendMessage, isStreaming, cancelStream } = useAgentContext();

  // sendMessage / cancelStream context'ten geliyor; her re-render'da yeni
  // referans donebiliyor. Assistant-UI runtime adapter degistiginde icteki
  // converter'lari yeniden import ediyor — bu da gereksiz repaint'e yol
  // aciyor. Ref ile stabil callback'lere sariyoruz.
  const sendRef = useRef(sendMessage);
  sendRef.current = sendMessage;
  const cancelRef = useRef(cancelStream);
  cancelRef.current = cancelStream;

  const onNew = useCallback(async (msg: AppendMessage) => {
    const textPart = msg.content.find((p) => p.type === 'text');
    const text = textPart && 'text' in textPart ? (textPart.text as string) : '';
    if (text.trim()) sendRef.current(text);
  }, []);

  const onCancel = useCallback(async () => {
    cancelRef.current();
  }, []);

  const visibleMessages = useMemo(() => {
    const filtered = messages.filter(
      (m) => !(m.role === 'tool' && m.toolName && HIDDEN_TOOLS.has(m.toolName))
    );
    // Ayni assistant turn'unde tool call'larini metin cevabindan ONCE goster.
    // LLM bazen once metin uretip sonra tool cagirdigi icin chronological sira
    // gorsel olarak "cevabin altinda loose tool call" hissi veriyor.
    const reordered: ChatMessage[] = [];
    for (let i = 0; i < filtered.length; ) {
      const head = filtered[i];
      if (head.role === 'user') {
        reordered.push(head);
        i++;
        continue;
      }
      const group: ChatMessage[] = [];
      while (i < filtered.length && filtered[i].role !== 'user') {
        group.push(filtered[i]);
        i++;
      }
      const tools = group.filter((g) => g.role === 'tool');
      const texts = group.filter((g) => g.role !== 'tool');
      reordered.push(...tools, ...texts);
    }
    return reordered;
  }, [messages]);

  const runtime = useExternalStoreRuntime<ChatMessage>({
    messages: visibleMessages,
    convertMessage,
    isRunning: isStreaming,
    onNew,
    onCancel,
  });

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
