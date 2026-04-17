import { useState, useEffect, useRef } from 'react';
import {
  Box,
  Flex,
  Text,
  IconButton,
  useColorMode,
} from '@chakra-ui/react';
import { CloseIcon } from '@chakra-ui/icons';
import { useAgentContext } from '../context/AgentContext';

export default function NotificationBar() {
  const { notifications, sseConnected, activeAgent } = useAgentContext();
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());
  const [showDisconnect, setShowDisconnect] = useState(false);
  const disconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  useEffect(() => {
    if (disconnectTimer.current) clearTimeout(disconnectTimer.current);
    if (sseConnected) {
      setShowDisconnect(false);
    } else {
      disconnectTimer.current = setTimeout(() => setShowDisconnect(true), 3000);
    }
    return () => {
      if (disconnectTimer.current) clearTimeout(disconnectTimer.current);
    };
  }, [sseConnected, activeAgent?.agent_id]);

  // chat_message_* event'leri AgentContext tarafindan dogrudan chat'e
  // enjekte edildigi icin notification bar'da gosterme; batch_complete ise
  // hem chat'e (inject_system_event) hem buraya gelir, kullaniciya hizli
  // bir banner ozeti versin.
  const HIDDEN_EVENTS = new Set([
    'heartbeat',
    'chat_message_chunk',
    'chat_message_injected',
    'chat_message_injected_start',
    'chat_message_injected_error',
    'tool_call',
    'tool_result',
  ]);

  const visible = notifications
    .filter((n) => !dismissed.has(n.timestamp) && !HIDDEN_EVENTS.has(n.event_type))
    .slice(0, 3);

  if (visible.length === 0 && !showDisconnect) return null;

  const dismiss = (ts: number) => {
    setDismissed((prev) => new Set([...prev, ts]));
  };

  const eventLabel = (type: string) => {
    switch (type) {
      case 'document_complete':
        return 'Belge tamamlandi';
      case 'batch_complete':
        return 'Batch tamamlandi';
      case 'extraction_error':
        return 'Extraction hatasi';
      case 'discovery':
        return 'Yeni kesif';
      default:
        return type;
    }
  };

  const eventColorScheme = (type: string): { bg: string; border: string } => {
    if (type.includes('error') || type.includes('fail'))
      return {
        bg: isDark ? 'rgba(254, 202, 202, 0.1)' : 'red.50',
        border: isDark ? 'red.700' : 'red.200',
      };
    if (type.includes('complete'))
      return {
        bg: isDark ? 'rgba(154, 230, 180, 0.1)' : 'green.50',
        border: isDark ? 'green.700' : 'green.200',
      };
    return {
      bg: isDark ? 'rgba(144, 205, 244, 0.1)' : 'blue.50',
      border: isDark ? 'blue.700' : 'blue.200',
    };
  };

  return (
    <Box
      px={4}
      py={2}
      bg={isDark ? 'gray.800' : 'gray.50'}
      borderBottom="1px"
      borderColor={isDark ? 'gray.700' : 'gray.200'}
    >
      {showDisconnect && (
        <Flex align="center" gap={1} fontSize="xs" color="gray.400" mb={visible.length > 0 ? 1 : 0}>
          <Box w="1.5" h="1.5" bg="gray.400" borderRadius="full" />
          <Text>SSE baglantisi kesildi, yeniden deneniyor...</Text>
        </Flex>
      )}
      {visible.map((n) => {
        const colors = eventColorScheme(n.event_type);
        return (
          <Flex
            key={n.timestamp}
            align="center"
            justify="space-between"
            px={3}
            py={1.5}
            mb={1}
            borderRadius="md"
            border="1px"
            borderColor={colors.border}
            bg={colors.bg}
            fontSize="xs"
          >
            <Flex align="center" gap={2} minW={0}>
              <Text fontWeight="medium" flexShrink={0}>
                {eventLabel(n.event_type)}
              </Text>
              {n.data.doc_id && (
                <Text color="gray.500" isTruncated>
                  {String(n.data.doc_id)}
                </Text>
              )}
              {n.event_type === 'batch_complete' && (() => {
                const summary = (n.data?.summary as Record<string, unknown>) || {};
                const total = Number(summary.total || 0);
                const done = Number(summary.completed || 0);
                const failed = Number(summary.failed || 0);
                if (!total) return null;
                return (
                  <Text color="gray.500" isTruncated>
                    {done}/{total} basarili{failed ? ` · ${failed} hatali` : ''}
                  </Text>
                );
              })()}
              {n.data.error_message && (
                <Text color="red.500" isTruncated>
                  {String(n.data.error_message)}
                </Text>
              )}
            </Flex>
            <IconButton
              aria-label="Kapat"
              icon={<CloseIcon boxSize={2} />}
              size="xs"
              variant="ghost"
              onClick={() => dismiss(n.timestamp)}
            />
          </Flex>
        );
      })}
    </Box>
  );
}
