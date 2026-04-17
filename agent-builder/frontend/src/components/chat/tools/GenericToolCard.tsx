import { Box, Text, useColorMode } from '@chakra-ui/react';
import { SettingsIcon } from '@chakra-ui/icons';
import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import CompactToolWrapper from './CompactToolWrapper';

const MAX_PREVIEW = 600;

function formatResult(result: unknown): string {
  if (result == null) return '';
  if (typeof result === 'string') return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

export default function GenericToolCard(props: ToolCallMessagePartProps) {
  const { toolName, args, result, status } = props;
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  const running = status?.type === 'running';
  const errored = status?.type === 'incomplete' || (props as { isError?: boolean }).isError === true;
  const resultText = formatResult(result);
  const preview = resultText.length > MAX_PREVIEW ? resultText.slice(0, MAX_PREVIEW) + '…' : resultText;
  const argsText = args && Object.keys(args).length > 0 ? JSON.stringify(args, null, 2) : '';

  const summary = resultText
    ? resultText.split('\n')[0].slice(0, 80)
    : argsText
    ? argsText.split('\n').filter((l) => l.trim()).join(' ').slice(0, 80)
    : '';

  return (
    <CompactToolWrapper
      toolName={toolName}
      icon={SettingsIcon}
      colorScheme={errored ? 'red' : 'gray'}
      status={running ? 'running' : errored ? 'error' : 'done'}
      summary={summary}
    >
      {(argsText || resultText) && (
        <Box px={3} py={2}>
          {argsText && (
            <Box mb={resultText ? 2 : 0}>
              <Text fontSize="2xs" fontWeight="bold" color="gray.500" mb={1}>
                ARGS
              </Text>
              <Box
                as="pre"
                whiteSpace="pre-wrap"
                fontFamily="mono"
                fontSize="2xs"
                p={2}
                borderRadius="md"
                bg={isDark ? 'gray.900' : 'gray.50'}
                border="1px solid"
                borderColor={isDark ? 'gray.700' : 'gray.200'}
                maxH="40"
                overflowY="auto"
              >
                {argsText}
              </Box>
            </Box>
          )}
          {resultText && (
            <Box>
              <Text fontSize="2xs" fontWeight="bold" color="gray.500" mb={1}>
                RESULT
              </Text>
              <Box
                as="pre"
                whiteSpace="pre-wrap"
                fontFamily="mono"
                fontSize="2xs"
                p={2}
                borderRadius="md"
                bg={isDark ? 'gray.900' : 'gray.50'}
                border="1px solid"
                borderColor={isDark ? 'gray.700' : 'gray.200'}
                maxH="80"
                overflowY="auto"
              >
                {preview}
              </Box>
            </Box>
          )}
        </Box>
      )}
    </CompactToolWrapper>
  );
}
