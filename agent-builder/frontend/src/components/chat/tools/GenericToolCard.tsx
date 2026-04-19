import { Box, Text, useColorMode } from '@chakra-ui/react';
import { SettingsIcon } from '@chakra-ui/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ToolCallMessagePartProps } from '@assistant-ui/react';
import CompactToolWrapper from './CompactToolWrapper';

const MAX_PREVIEW = 800;

function isMarkdownLike(text: string): boolean {
  return /^#{1,4}\s|^\*\*|^-\s|^\d+\.\s|^>\s|^\|.*\|/m.test(text);
}

function isJsonLike(text: string): boolean {
  const trimmed = text.trim();
  return (trimmed.startsWith('{') || trimmed.startsWith('[')) && (trimmed.endsWith('}') || trimmed.endsWith(']'));
}

function formatResult(result: unknown): string {
  if (result == null) return '';
  if (typeof result === 'string') return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

function ResultContent({ text, isDark }: { text: string; isDark: boolean }) {
  if (isMarkdownLike(text)) {
    return (
      <Box
        fontSize="xs"
        lineHeight="1.6"
        color={isDark ? 'gray.200' : 'gray.700'}
        sx={{
          '& h1, & h2, & h3, & h4': {
            fontWeight: 'bold',
            mt: 2,
            mb: 1,
            color: isDark ? 'gray.100' : 'gray.800',
          },
          '& h3': { fontSize: 'xs', color: isDark ? 'blue.200' : 'blue.700' },
          '& h4': { fontSize: 'xs', color: isDark ? 'purple.200' : 'purple.700' },
          '& ul, & ol': { pl: 4, mb: 1 },
          '& li': { mb: 0.5 },
          '& strong': { color: isDark ? 'gray.50' : 'gray.900' },
          '& code': {
            px: 1,
            py: 0.5,
            borderRadius: 'sm',
            bg: isDark ? 'gray.700' : 'gray.100',
            fontSize: '2xs',
            fontFamily: 'mono',
          },
          '& hr': { my: 2, borderColor: isDark ? 'gray.600' : 'gray.200' },
          '& table': {
            w: 'full',
            fontSize: '2xs',
            borderCollapse: 'collapse',
          },
          '& th': {
            px: 2,
            py: 1,
            textAlign: 'left',
            fontWeight: 'bold',
            bg: isDark ? 'gray.700' : 'gray.100',
            borderBottom: '1px solid',
            borderColor: isDark ? 'gray.600' : 'gray.200',
          },
          '& td': {
            px: 2,
            py: 1,
            borderBottom: '1px solid',
            borderColor: isDark ? 'gray.700' : 'gray.100',
          },
          '& blockquote': {
            pl: 3,
            borderLeft: '3px solid',
            borderColor: isDark ? 'blue.400' : 'blue.300',
            color: isDark ? 'gray.400' : 'gray.500',
            fontStyle: 'italic',
          },
        }}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </Box>
    );
  }

  return (
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
      color={isDark ? 'gray.300' : 'gray.700'}
    >
      {text}
    </Box>
  );
}

export default function GenericToolCard(props: ToolCallMessagePartProps) {
  const { toolName, args, result, status } = props;
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  const running = status?.type === 'running';
  const errored = status?.type === 'incomplete' || (props as { isError?: boolean }).isError === true;
  const resultText = formatResult(result);
  const preview = resultText.length > MAX_PREVIEW ? resultText.slice(0, MAX_PREVIEW) + '\u2026' : resultText;
  const argsText = args && Object.keys(args).length > 0 ? JSON.stringify(args, null, 2) : '';

  const summary = resultText
    ? resultText.split('\n')[0].slice(0, 80)
    : argsText
    ? argsText.split('\n').filter((l) => l.trim()).join(' ').slice(0, 80)
    : '';

  const showArgsAsCode = argsText && isJsonLike(argsText);

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
              <Text fontSize="2xs" fontWeight="bold" color="gray.500" mb={1} textTransform="uppercase" letterSpacing="wider">
                Args
              </Text>
              {showArgsAsCode ? (
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
                  color={isDark ? 'gray.300' : 'gray.700'}
                >
                  {argsText}
                </Box>
              ) : (
                <Text fontSize="2xs" color={isDark ? 'gray.300' : 'gray.600'}>
                  {argsText}
                </Text>
              )}
            </Box>
          )}
          {resultText && (
            <Box>
              <Text fontSize="2xs" fontWeight="bold" color="gray.500" mb={1} textTransform="uppercase" letterSpacing="wider">
                Result
              </Text>
              <ResultContent text={preview} isDark={isDark} />
            </Box>
          )}
        </Box>
      )}
    </CompactToolWrapper>
  );
}
