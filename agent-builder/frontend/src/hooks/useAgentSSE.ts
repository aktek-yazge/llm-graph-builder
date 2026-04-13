import { useEffect, useRef, useCallback, useState } from 'react';
import { agentBuilderUrl, PREFIX, type AgentNotification } from '../services/evolvingApi';

export function useAgentSSE(agentId: string | null) {
  const [notifications, setNotifications] = useState<AgentNotification[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  const connect = useCallback(() => {
    if (!agentId) return;
    esRef.current?.close();

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
      setConnected(false);
      es.close();
      setTimeout(connect, 5000);
    };
  }, [agentId]);

  useEffect(() => {
    connect();
    return () => {
      esRef.current?.close();
      esRef.current = null;
      setConnected(false);
    };
  }, [connect]);

  const clearNotifications = useCallback(() => setNotifications([]), []);

  return { notifications, connected, clearNotifications };
}
