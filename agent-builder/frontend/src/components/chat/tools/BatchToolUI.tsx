import { Box, Flex, HStack, Icon, Text, Badge, Progress, useColorMode, Button } from '@chakra-ui/react';
import { TriangleUpIcon, CheckIcon, WarningIcon, TimeIcon } from '@chakra-ui/icons';
import { makeAssistantToolUI } from '@assistant-ui/react';
import CompactToolWrapper from './CompactToolWrapper';

type BatchArgs = {
  file_paths?: string[];
  total_files?: number;
  batch_name?: string;
  approve?: boolean;
};

function BatchApprovalCard({ args, result, status }: { args?: BatchArgs; result?: unknown; status?: { type: string } }) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const fileCount = args?.file_paths?.length ?? args?.total_files ?? 0;
  const name = args?.batch_name || 'Batch islemi';
  const running = status?.type === 'running';
  const resultText = typeof result === 'string' ? result : result ? JSON.stringify(result) : '';

  const summary = `${name} · ${fileCount} dosya${running ? ' · onay bekliyor' : ''}`;

  return (
    <CompactToolWrapper
      toolName="Batch Islemi"
      icon={TriangleUpIcon}
      colorScheme={running ? 'yellow' : 'green'}
      status={running ? 'running' : 'done'}
      summary={summary}
      badges={
        <Badge colorScheme={running ? 'yellow' : 'green'} variant="subtle" fontSize="2xs">
          {running ? 'HITL' : 'Baslatildi'}
        </Badge>
      }
    >
      <Box px={3} py={2}>
        <Text fontSize="sm" fontWeight="semibold" color={isDark ? 'gray.100' : 'gray.800'}>
          {name}
        </Text>
        <HStack spacing={3} mt={1} fontSize="xs" color={isDark ? 'gray.300' : 'gray.600'}>
          <HStack spacing={1}>
            <Icon as={TimeIcon} boxSize={3} />
            <Text>{fileCount} dosya</Text>
          </HStack>
          {args?.approve === true && (
            <Badge colorScheme="green" variant="subtle">
              onaylandi
            </Badge>
          )}
        </HStack>
        {resultText && (
          <Text fontSize="xs" color={isDark ? 'gray.400' : 'gray.600'} mt={2}>
            {resultText.length > 220 ? resultText.slice(0, 220) + '…' : resultText}
          </Text>
        )}
        {running && (
          <HStack spacing={2} mt={3}>
            <Button size="xs" colorScheme="green" leftIcon={<CheckIcon />} isDisabled>
              Onayla
            </Button>
            <Button size="xs" colorScheme="red" variant="outline" leftIcon={<WarningIcon />} isDisabled>
              Reddet
            </Button>
            <Text fontSize="2xs" color="gray.500" fontStyle="italic">
              (Onay sag panelden yapiliyor)
            </Text>
          </HStack>
        )}
      </Box>
    </CompactToolWrapper>
  );
}

type ProgressShape = {
  processed?: number;
  total?: number;
  failed?: number;
  status?: string;
  current_file?: string;
};

function extractProgress(result: unknown): ProgressShape {
  if (!result) return {};
  if (typeof result === 'object') return result as ProgressShape;
  if (typeof result === 'string') {
    const trimmed = result.trim();
    if (trimmed.startsWith('{')) {
      try {
        return JSON.parse(trimmed) as ProgressShape;
      } catch {
        return {};
      }
    }
  }
  return {};
}

function BatchProgressCard({ result }: { result: unknown }) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const p = extractProgress(result);
  const processed = p.processed ?? 0;
  const total = p.total ?? 0;
  const failed = p.failed ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;

  return (
    <CompactToolWrapper
      toolName="Batch Ilerleme"
      icon={TimeIcon}
      colorScheme="blue"
      summary={`${processed}/${total} (%${pct})${failed > 0 ? ` · ${failed} hata` : ''}`}
      badges={
        p.status ? (
          <Badge colorScheme="blue" variant="subtle" fontSize="2xs">
            {p.status}
          </Badge>
        ) : null
      }
    >
      <Box px={3} py={2}>
        <Flex justify="space-between" fontSize="xs" mb={1}>
          <Text color={isDark ? 'gray.300' : 'gray.600'}>
            {processed}/{total} dosya
          </Text>
          <Text color={isDark ? 'gray.100' : 'gray.800'} fontWeight="semibold">
            {pct}%
          </Text>
        </Flex>
        <Progress value={pct} size="sm" colorScheme="blue" borderRadius="full" />
        {failed > 0 && (
          <HStack spacing={1} mt={2} fontSize="xs" color="red.400">
            <Icon as={WarningIcon} boxSize={3} />
            <Text>{failed} dosya basarisiz</Text>
          </HStack>
        )}
        {p.current_file && (
          <Text fontSize="2xs" color={isDark ? 'gray.400' : 'gray.500'} mt={2} fontFamily="mono" noOfLines={1}>
            {p.current_file}
          </Text>
        )}
      </Box>
    </CompactToolWrapper>
  );
}

export const StartBatchProcessingToolUI = makeAssistantToolUI<BatchArgs, unknown>({
  toolName: 'start_batch_processing',
  render: ({ args, result, status }) => <BatchApprovalCard args={args} result={result} status={status} />,
});

export const GetBatchProgressToolUI = makeAssistantToolUI<Record<string, unknown>, unknown>({
  toolName: 'get_batch_progress',
  render: ({ result }) => <BatchProgressCard result={result} />,
});
