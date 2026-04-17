import { useMemo } from 'react';
import {
  Box, Flex, HStack, VStack, Text, Badge, Button, Tooltip, Divider,
  Tag, TagLabel, Alert, AlertIcon,
} from '@chakra-ui/react';
import { EditIcon, DeleteIcon, TimeIcon } from '@chakra-ui/icons';
import ReactMarkdown, { Components } from 'react-markdown';
import type { WikiPageDetail } from '../../services/evolvingApi';
import {
  CATEGORY_COLORS, CATEGORY_LABELS, wikilinksToMarkdown, formatTimestamp, pageName,
} from './wikiUtils';

interface Props {
  page: WikiPageDetail;
  onEdit: () => void;
  onDelete: () => void;
  onNavigate: (path: string) => void;
  onOpenHistory: () => void;
}

export default function WikiPageView({
  page, onEdit, onDelete, onNavigate, onOpenHistory,
}: Props) {
  const rendered = useMemo(() => wikilinksToMarkdown(page.content), [page.content]);
  const cat = page.category in CATEGORY_LABELS ? page.category : 'general';

  const components: Components = {
    a: ({ href, children, ...rest }) => {
      if (href && href.startsWith('wikilink://')) {
        const target = decodeURIComponent(href.slice('wikilink://'.length));
        const broken = page.broken_links.includes(target);
        return (
          <button
            type="button"
            onClick={() => onNavigate(target)}
            style={{
              color: broken ? '#dc2626' : '#4f46e5',
              textDecoration: broken ? 'line-through' : 'underline',
              background: 'none',
              border: 'none',
              padding: 0,
              font: 'inherit',
              cursor: 'pointer',
            }}
            title={broken ? `Cozulemeyen bag: ${target}` : target}
          >
            {children}
          </button>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer" {...rest}>
          {children}
        </a>
      );
    },
    h1: ({ children }) => (
      <Text as="h1" fontSize="2xl" fontWeight="bold" mt={4} mb={2}>
        {children}
      </Text>
    ),
    h2: ({ children }) => (
      <Text as="h2" fontSize="xl" fontWeight="bold" mt={4} mb={2} borderBottom="1px solid" borderColor="gray.200" pb={1}>
        {children}
      </Text>
    ),
    h3: ({ children }) => (
      <Text as="h3" fontSize="lg" fontWeight="semibold" mt={3} mb={2}>
        {children}
      </Text>
    ),
    p: ({ children }) => (
      <Text fontSize="sm" lineHeight="1.7" mb={3}>
        {children}
      </Text>
    ),
    ul: ({ children }) => (
      <Box as="ul" fontSize="sm" pl={6} mb={3} style={{ listStyle: 'disc' }}>
        {children}
      </Box>
    ),
    ol: ({ children }) => (
      <Box as="ol" fontSize="sm" pl={6} mb={3} style={{ listStyle: 'decimal' }}>
        {children}
      </Box>
    ),
    li: ({ children }) => <Box as="li" mb={1}>{children}</Box>,
    code: ({ children, className }) => {
      const inline = !className;
      if (inline) {
        return (
          <Box as="code" px={1} py={0.5} bg="gray.100" color="gray.800" borderRadius="sm" fontSize="xs">
            {children}
          </Box>
        );
      }
      return (
        <Box as="pre" bg="gray.50" p={3} borderRadius="md" overflowX="auto" my={2} borderLeft="3px solid" borderColor="indigo.300">
          <Box as="code" fontSize="xs" whiteSpace="pre" fontFamily="mono">{children}</Box>
        </Box>
      );
    },
    blockquote: ({ children }) => (
      <Box pl={3} borderLeft="3px solid" borderColor="gray.300" color="gray.600" my={2}>
        {children}
      </Box>
    ),
    table: ({ children }) => (
      <Box as="table" fontSize="sm" my={3} w="100%" sx={{ borderCollapse: 'collapse' }}>
        {children}
      </Box>
    ),
    th: ({ children }) => (
      <Box as="th" border="1px solid" borderColor="gray.200" px={2} py={1} bg="gray.50" fontWeight="semibold" textAlign="left">
        {children}
      </Box>
    ),
    td: ({ children }) => (
      <Box as="td" border="1px solid" borderColor="gray.200" px={2} py={1}>
        {children}
      </Box>
    ),
  };

  return (
    <Flex direction="column" h="100%" overflow="hidden" bg="white">
      <Flex
        px={6}
        py={3}
        borderBottom="1px solid"
        borderColor="gray.200"
        align="center"
        gap={3}
        flexShrink={0}
      >
        <Box
          w="10px"
          h="10px"
          borderRadius="full"
          bg={CATEGORY_COLORS[cat as keyof typeof CATEGORY_COLORS]}
        />
        <Box flex={1} minW={0}>
          <Text fontSize="lg" fontWeight="bold" noOfLines={1}>
            {pageName(page.path)}
          </Text>
          <Text fontSize="xs" color="gray.500" noOfLines={1}>{page.path}</Text>
        </Box>
        <Tag size="sm" colorScheme="gray">
          <TagLabel>v{page.version}</TagLabel>
        </Tag>
        <Tooltip label="Surum gecmisi">
          <Button size="sm" variant="ghost" leftIcon={<TimeIcon />} onClick={onOpenHistory}>
            {formatTimestamp(page.created_at)}
          </Button>
        </Tooltip>
        <Button size="sm" leftIcon={<EditIcon />} colorScheme="indigo" onClick={onEdit}>
          Duzenle
        </Button>
        <Button size="sm" variant="ghost" leftIcon={<DeleteIcon />} onClick={onDelete}>
          Sil
        </Button>
      </Flex>

      {page.broken_links.length > 0 && (
        <Alert status="warning" size="sm" flexShrink={0}>
          <AlertIcon />
          <Text fontSize="xs">
            Cozulemeyen baglar: {page.broken_links.join(', ')}
          </Text>
        </Alert>
      )}

      <Box flex={1} overflowY="auto" px={6} py={4}>
        <ReactMarkdown components={components}>{rendered}</ReactMarkdown>

        <Divider my={6} />
        <VStack align="stretch" spacing={2}>
          <Text fontSize="sm" fontWeight="semibold">Backlinks</Text>
          {page.backlinks.length === 0 ? (
            <Text fontSize="xs" color="gray.500">Bu sayfaya baglanti veren sayfa yok.</Text>
          ) : (
            <HStack wrap="wrap" spacing={2}>
              {page.backlinks.map((b) => (
                <Badge
                  key={b}
                  as="button"
                  colorScheme="indigo"
                  cursor="pointer"
                  onClick={() => onNavigate(b)}
                  px={2}
                  py={1}
                >
                  {b}
                </Badge>
              ))}
            </HStack>
          )}

          {page.links.length > 0 && (
            <>
              <Text fontSize="sm" fontWeight="semibold" mt={4}>Disari baglantilar</Text>
              <HStack wrap="wrap" spacing={2}>
                {page.links.map((l) => (
                  <Tag key={l} size="sm" colorScheme="gray">{l}</Tag>
                ))}
              </HStack>
            </>
          )}
        </VStack>
      </Box>
    </Flex>
  );
}
