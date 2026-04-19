import { Box, Text, useColorMode, Divider } from '@chakra-ui/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { TextMessagePartComponent } from '@assistant-ui/react';
import type { Components } from 'react-markdown';
import PlanCard, { parsePlan } from './PlanCard';

function useMarkdownComponents(): Components {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  return {
    h1: ({ children }) => (
      <Text
        as="h1"
        fontSize="lg"
        fontWeight="bold"
        color={isDark ? 'gray.50' : 'gray.900'}
        mt={3}
        mb={2}
        lineHeight="1.3"
      >
        {children}
      </Text>
    ),
    h2: ({ children }) => (
      <Text
        as="h2"
        fontSize="md"
        fontWeight="bold"
        color={isDark ? 'gray.100' : 'gray.800'}
        mt={3}
        mb={1.5}
        lineHeight="1.3"
      >
        {children}
      </Text>
    ),
    h3: ({ children }) => (
      <Text
        as="h3"
        fontSize="sm"
        fontWeight="bold"
        color={isDark ? 'blue.200' : 'blue.700'}
        mt={3}
        mb={1.5}
        lineHeight="1.4"
      >
        {children}
      </Text>
    ),
    h4: ({ children }) => (
      <Text
        as="h4"
        fontSize="sm"
        fontWeight="semibold"
        color={isDark ? 'purple.200' : 'purple.700'}
        mt={2}
        mb={1}
        lineHeight="1.4"
      >
        {children}
      </Text>
    ),
    p: ({ children }) => (
      <Text
        fontSize="sm"
        color={isDark ? 'gray.200' : 'gray.700'}
        mb={2}
        lineHeight="1.65"
      >
        {children}
      </Text>
    ),
    strong: ({ children }) => (
      <Text as="strong" fontWeight="bold" color={isDark ? 'gray.50' : 'gray.900'}>
        {children}
      </Text>
    ),
    em: ({ children }) => (
      <Text as="em" fontStyle="italic" color={isDark ? 'gray.300' : 'gray.600'}>
        {children}
      </Text>
    ),
    ul: ({ children }) => (
      <Box as="ul" pl={4} mb={2} sx={{ listStyleType: 'disc' }}>
        {children}
      </Box>
    ),
    ol: ({ children }) => (
      <Box as="ol" pl={4} mb={2} sx={{ listStyleType: 'decimal' }}>
        {children}
      </Box>
    ),
    li: ({ children }) => (
      <Box
        as="li"
        fontSize="sm"
        color={isDark ? 'gray.200' : 'gray.700'}
        mb={0.5}
        lineHeight="1.6"
        sx={{ '&::marker': { color: isDark ? 'blue.300' : 'blue.500' } }}
      >
        {children}
      </Box>
    ),
    hr: () => (
      <Divider
        my={2.5}
        borderColor={isDark ? 'gray.600' : 'gray.200'}
      />
    ),
    code: ({ className, children }) => {
      const isBlock = className?.includes('language-');
      if (isBlock) {
        return (
          <Box
            as="pre"
            my={2}
            p={3}
            borderRadius="lg"
            bg={isDark ? 'gray.900' : 'gray.50'}
            border="1px solid"
            borderColor={isDark ? 'gray.700' : 'gray.200'}
            overflowX="auto"
            fontSize="xs"
            fontFamily="mono"
            lineHeight="1.6"
            color={isDark ? 'green.200' : 'gray.800'}
          >
            <Box as="code">{children}</Box>
          </Box>
        );
      }
      return (
        <Box
          as="code"
          px={1.5}
          py={0.5}
          borderRadius="md"
          bg={isDark ? 'gray.700' : 'gray.100'}
          color={isDark ? 'orange.200' : 'orange.600'}
          fontSize="xs"
          fontFamily="mono"
          fontWeight="medium"
        >
          {children}
        </Box>
      );
    },
    blockquote: ({ children }) => (
      <Box
        as="blockquote"
        pl={3}
        my={2}
        borderLeft="3px solid"
        borderColor={isDark ? 'blue.400' : 'blue.300'}
        color={isDark ? 'gray.300' : 'gray.600'}
        fontStyle="italic"
      >
        {children}
      </Box>
    ),
    table: ({ children }) => (
      <Box overflowX="auto" my={2}>
        <Box
          as="table"
          w="full"
          fontSize="xs"
          borderRadius="lg"
          overflow="hidden"
          border="1px solid"
          borderColor={isDark ? 'gray.700' : 'gray.200'}
          sx={{ borderCollapse: 'collapse' }}
        >
          {children}
        </Box>
      </Box>
    ),
    thead: ({ children }) => (
      <Box
        as="thead"
        bg={isDark ? 'gray.700' : 'gray.100'}
      >
        {children}
      </Box>
    ),
    tbody: ({ children }) => (
      <Box as="tbody">{children}</Box>
    ),
    tr: ({ children }) => (
      <Box
        as="tr"
        borderBottom="1px solid"
        borderColor={isDark ? 'gray.700' : 'gray.200'}
        _hover={{ bg: isDark ? 'gray.750' : 'gray.50' }}
      >
        {children}
      </Box>
    ),
    th: ({ children }) => (
      <Box
        as="th"
        px={3}
        py={1.5}
        textAlign="left"
        fontSize="xs"
        fontWeight="bold"
        color={isDark ? 'gray.200' : 'gray.700'}
        textTransform="uppercase"
        letterSpacing="wider"
      >
        {children}
      </Box>
    ),
    td: ({ children }) => (
      <Box
        as="td"
        px={3}
        py={1.5}
        fontSize="xs"
        color={isDark ? 'gray.300' : 'gray.600'}
      >
        {children}
      </Box>
    ),
    a: ({ children, href }) => (
      <Text
        as="a"
        href={href}
        color={isDark ? 'blue.300' : 'blue.600'}
        textDecoration="underline"
        textDecorationStyle="dotted"
        _hover={{ textDecorationStyle: 'solid' }}
        target="_blank"
        rel="noopener noreferrer"
      >
        {children}
      </Text>
    ),
  };
}

const PlanAwareText: TextMessagePartComponent = ({ text }) => {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const components = useMarkdownComponents();

  if (!text) return <Box as="span" color="gray.500">&hellip;</Box>;

  const plan = parsePlan(text);
  if (plan) {
    return (
      <Box>
        {plan.header && (
          <Box mb={1}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
              {plan.header}
            </ReactMarkdown>
          </Box>
        )}
        <PlanCard plan={plan} />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        '& > *:first-of-type': { mt: 0 },
        '& > *:last-child': { mb: 0 },
      }}
      color={isDark ? 'gray.200' : 'gray.700'}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </Box>
  );
};

export default PlanAwareText;
