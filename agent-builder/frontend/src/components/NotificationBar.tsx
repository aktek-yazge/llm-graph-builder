import { useState } from 'react';
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
  const { notifications, sseConnected } = useAgentContext();
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  const visible = notifications
    .filter((n) => !dismissed.has(n.timestamp) && n.event_type !== 'heartbeat')
    .slice(0, 3);

  if (visible.length === 0 && sseConnected) return null;

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
      {!sseConnected && (
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
