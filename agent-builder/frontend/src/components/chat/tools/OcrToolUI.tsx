import {
  Box,
  HStack,
  Icon,
  Text,
  Badge,
  Button,
  useColorMode,
  useDisclosure,
} from '@chakra-ui/react';
import { AttachmentIcon, ViewIcon } from '@chakra-ui/icons';
import { makeAssistantToolUI } from '@assistant-ui/react';
import { useAgentContext } from '../../../context/AgentContext';
import OcrPreviewModal from '../../resources/OcrPreviewModal';
import CompactToolWrapper from './CompactToolWrapper';

type OcrArgs = {
  file_path?: string;
  path?: string;
  filename?: string;
  pages?: number;
  strategy?: string;
};

type OcrResultShape = {
  status?: string;
  file_name?: string;
  doc_key?: string;
  page_count?: number;
  total_chars?: number;
  duration_ms?: number;
  token_usage?: {
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
    cost_usd?: number;
  };
  first_line?: string;
  stored_as?: string;
};

function toResultShape(result: unknown): OcrResultShape | null {
  if (!result) return null;
  if (typeof result === 'object') return result as OcrResultShape;
  if (typeof result === 'string') {
    const trimmed = result.trim();
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try { return JSON.parse(trimmed) as OcrResultShape; } catch { return null; }
    }
  }
  return null;
}

function formatDuration(ms: number): string {
  if (!ms || ms < 0) return '';
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return `${m}m ${rem}s`;
}

function formatNumber(n: number | undefined): string {
  if (n == null) return '';
  return n.toLocaleString('tr-TR');
}

function formatCost(cost: number | undefined): string {
  if (cost == null || cost <= 0) return '';
  if (cost < 0.001) return `$${cost.toFixed(5)}`;
  if (cost < 1) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

function shortName(name: string, max = 32): string {
  if (name.length <= max) return name;
  const ext = name.lastIndexOf('.');
  if (ext > 0 && name.length - ext < 8) {
    return name.slice(0, max - (name.length - ext) - 3) + '…' + name.slice(ext);
  }
  return name.slice(0, max - 1) + '…';
}

function OcrCard({
  title,
  args,
  result,
  running,
}: {
  title: string;
  args?: OcrArgs;
  result?: unknown;
  running?: boolean;
}) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const { activeAgent } = useAgentContext();
  const previewModal = useDisclosure();

  const shape = toResultShape(result);
  const file =
    shape?.file_name ||
    args?.file_path ||
    args?.path ||
    args?.filename ||
    'dosya';

  const docKey = shape?.doc_key || shape?.stored_as || '';
  const pages = shape?.page_count ?? args?.pages;
  const chars = shape?.total_chars;
  const duration = shape?.duration_ms;
  const tokens = shape?.token_usage?.total_tokens;
  const cost = shape?.token_usage?.cost_usd;
  const firstLine = shape?.first_line;

  const summary = `${shortName(file)}${pages != null ? ` · ${pages} sf` : ''}`;

  return (
    <>
      <CompactToolWrapper
        toolName={title}
        icon={ViewIcon}
        colorScheme="orange"
        status={running ? 'running' : 'done'}
        summary={summary}
      >
        <Box px={3} py={2.5}>
          <HStack spacing={1.5} fontSize="2xs" color={isDark ? 'gray.300' : 'gray.700'} flexWrap="wrap" mb={2}>
            <Icon as={AttachmentIcon} boxSize={3} />
            <Text fontFamily="mono" noOfLines={1} maxW="60%">
              {file}
            </Text>
            {pages != null && (
              <Badge fontSize="2xs" colorScheme="gray" variant="subtle">
                {pages} sayfa
              </Badge>
            )}
            {chars != null && chars > 0 && (
              <Badge fontSize="2xs" colorScheme="gray" variant="subtle">
                {formatNumber(chars)} karakter
              </Badge>
            )}
            {duration != null && duration > 0 && (
              <Badge fontSize="2xs" colorScheme="blue" variant="subtle">
                {formatDuration(duration)}
              </Badge>
            )}
            {tokens != null && tokens > 0 && (
              <Badge fontSize="2xs" colorScheme="purple" variant="subtle">
                {formatNumber(tokens)} token
              </Badge>
            )}
            {cost != null && cost > 0 && (
              <Badge fontSize="2xs" colorScheme="green" variant="subtle">
                {formatCost(cost)}
              </Badge>
            )}
            {args?.strategy && (
              <Badge fontSize="2xs" colorScheme="orange" variant="subtle">
                {args.strategy}
              </Badge>
            )}
            {docKey && (
              <Badge fontSize="2xs" colorScheme="gray" variant="outline" fontFamily="mono">
                {docKey}
              </Badge>
            )}
          </HStack>
          {firstLine && (
            <Text
              fontSize="xs"
              fontFamily="mono"
              color={isDark ? 'gray.400' : 'gray.600'}
              noOfLines={2}
              mb={2}
            >
              {firstLine}
            </Text>
          )}
          {!running && docKey && activeAgent && (
            <Button
              size="xs"
              colorScheme="orange"
              variant="outline"
              leftIcon={<ViewIcon />}
              onClick={previewModal.onOpen}
            >
              Onizle
            </Button>
          )}
        </Box>
      </CompactToolWrapper>
      {docKey && activeAgent && (
        <OcrPreviewModal
          isOpen={previewModal.isOpen}
          onClose={previewModal.onClose}
          agentId={activeAgent.agent_id}
          docKey={docKey}
          fileName={file}
          totalPages={pages ?? 0}
        />
      )}
    </>
  );
}

export const RunOcrToolUI = makeAssistantToolUI<OcrArgs, unknown>({
  toolName: 'run_ocr',
  render: ({ args, result, status }) => (
    <OcrCard title="OCR" args={args} result={result} running={status?.type === 'running'} />
  ),
});

export const OcrAndAnalyzeToolUI = makeAssistantToolUI<OcrArgs, unknown>({
  toolName: 'ocr_and_analyze',
  render: ({ args, result, status }) => (
    <OcrCard title="OCR + Analiz" args={args} result={result} running={status?.type === 'running'} />
  ),
});
