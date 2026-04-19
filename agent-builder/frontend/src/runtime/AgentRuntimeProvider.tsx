import { useCallback, useMemo, useRef, type ReactNode } from 'react';
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type ThreadMessageLike,
  type AppendMessage,
} from '@assistant-ui/react';
import { useAgentContext, type ChatMessage } from '../context/AgentContext';

const HIDDEN_TOOLS = new Set(['write_todos', 'read_todos', 'get_current_plan']);

type JsonArgs = Record<string, unknown>;

export interface ToolStepInfo {
  toolName: string;
  args: Record<string, unknown>;
  result?: string;
  status: 'running' | 'done';
  id: string;
}

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

function buildMergedMessages(messages: ChatMessage[]): ThreadMessageLike[] {
  const filtered = messages.filter(
    (m) => !(m.role === 'tool' && m.toolName && HIDDEN_TOOLS.has(m.toolName))
  );

  const result: ThreadMessageLike[] = [];

  for (let i = 0; i < filtered.length; ) {
    const head = filtered[i];

    if (head.role === 'user') {
      result.push({
        id: head.id,
        role: 'user',
        content: [{ type: 'text', text: head.content || '' }],
        createdAt: new Date(head.timestamp),
      });
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

    const content: Array<
      | { type: 'text'; text: string }
      | { type: 'tool-call'; toolCallId: string; toolName: string; args: Record<string, never>; result?: string | undefined }
    > = [];

    if (tools.length >= 2) {
      const steps: ToolStepInfo[] = tools.map((t) => ({
        id: t.id,
        toolName: t.toolName || 'tool',
        args: t.toolInput ?? parseMaybeJSON(t.content) ?? {},
        result: t.content || undefined,
        status: t.content ? 'done' as const : 'running' as const,
      }));
      const allDone = steps.every((s) => s.status === 'done');
      content.push({
        type: 'tool-call' as const,
        toolCallId: `grp-${tools[0].id}`,
        toolName: '_tool_steps',
        args: { steps } as unknown as Record<string, never>,
        result: allDone ? JSON.stringify({ allDone: true }) : undefined,
      });
    } else if (tools.length === 1) {
      const t = tools[0];
      const toolName = t.toolName || 'tool';
      const args = t.toolInput ?? parseMaybeJSON(t.content) ?? {};
      content.push({
        type: 'tool-call' as const,
        toolCallId: t.id,
        toolName,
        args: args as unknown as Record<string, never>,
        result: t.content || undefined,
      });
    }

    for (const t of texts) {
      if (t.content) {
        content.push({ type: 'text' as const, text: t.content });
      }
    }

    if (content.length > 0) {
      const anchor = tools[0] || texts[0] || group[0];
      result.push({
        id: anchor.id,
        role: 'assistant',
        createdAt: new Date(anchor.timestamp),
        content,
      });
    }
  }

  return result;
}

export function AgentRuntimeProvider({ children }: { children: ReactNode }) {
  const { messages, sendMessage, isStreaming, cancelStream } = useAgentContext();

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

  const mergedMessages = useMemo(
    () => buildMergedMessages(messages),
    [messages]
  );

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages: mergedMessages,
    convertMessage: (m: ThreadMessageLike) => m,
    isRunning: isStreaming,
    onNew,
    onCancel,
  });

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
