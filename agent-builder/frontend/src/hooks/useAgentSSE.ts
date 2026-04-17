import { useEffect, useRef, useCallback, useState } from 'react';
import { agentBuilderUrl, PREFIX, type AgentNotification } from '../services/evolvingApi';

export function useAgentSSE(agentId: string | null) {
  const [notifications, setNotifications] = useState<AgentNotification[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const closedIntentionally = useRef(false);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cleanup = useCallback(() => {
    closedIntentionally.current = true;
    if (retryTimer.current) {
      clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  }, []);

  useEffect(() => {
    cleanup();
    setConnected(false);

    if (!agentId) return;

    closedIntentionally.current = false;

    const openConnection = () => {
      if (closedIntentionally.current) return;

      const url = `${agentBuilderUrl()}${PREFIX}/agents/${agentId}/notifications/stream`;
      const es = new EventSource(url);
      esRef.current = es;

      es.onopen = () => setConnected(true);

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as AgentNotification;
          if (data.event_type === 'heartbeat') return;
          setNotifications((prev) => [data, ...prev].slice(0, 200));
        } catch {
          /* skip */
        }
      };

      es.onerror = () => {
        es.close();
        esRef.current = null;
        if (closedIntentionally.current) return;
        setConnected(false);
        retryTimer.current = setTimeout(openConnection, 3000);
      };
    };

    openConnection();

    return cleanup;
  }, [agentId, cleanup]);

  const clearNotifications = useCallback(() => setNotifications([]), []);

  return { notifications, connected, clearNotifications };
}
