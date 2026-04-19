import { useMemo } from 'react';
import {
  Box, Flex, HStack, VStack, Text, Button, Tooltip,
} from '@chakra-ui/react';
import { Pencil, Trash2, Clock } from 'lucide-react';
import ReactMarkdown, { Components } from 'react-markdown';
import type { WikiPageDetail } from '../../services/evolvingApi';
import {
  CATEGORY_COLORS, CATEGORY_LABELS, wikilinksToMarkdown, pageName,
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
  const catColors = CATEGORY_COLORS[cat as keyof typeof CATEGORY_COLORS];

  const components: Components = {
    a: ({ href, children }) => {
      if (href && href.startsWith('wikilink://')) {
        const target = decodeURIComponent(href.slice('wikilink://'.length));
        const broken = page.broken_links.includes(target);
        return (
          <button
            type="button"
            onClick={() => onNavigate(target)}
            style={{
              color: broken ? '#ff6b6b' : '#4c6ef5',
              textDecoration: broken ? 'line-through' : 'none',
              background: 'none',
              border: 'none',
              padding: '0 2px',
              borderRadius: '3px',
              font: 'inherit',
              cursor: 'pointer',
              transition: 'background 0.12s',
            }}
            onMouseEnter={(e) => { (e.target as HTMLElement).style.background = '#f0f4ff'; }}
            onMouseLeave={(e) => { (e.target as HTMLElement).style.background = 'none'; }}
            title={broken ? `Cozulemeyen bag: ${target}` : target}
          >
            {children}
          </button>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer" style={{ color: '#4c6ef5', textDecoration: 'none' }}>
          {children}
        </a>
      );
    },
    h1: ({ children }) => (
      <Text as="h1" fontSize="22px" fontWeight="700" mt={6} mb={3} color="#212529" letterSpacing="-0.01em">
        {children}
      </Text>
    ),
    h2: ({ children }) => (
      <Text as="h2" fontSize="18px" fontWeight="600" mt={6} mb={2} color="#212529" pb={2} borderBottom="1px solid" borderColor="#f1f3f5">
        {children}
      </Text>
    ),
    h3: ({ children }) => (
      <Text as="h3" fontSize="15px" fontWeight="600" mt={4} mb={2} color="#212529">
        {children}
      </Text>
    ),
    p: ({ children }) => (
      <Text fontSize="14px" lineHeight="1.75" mb={3} color="#343a40">
        {children}
      </Text>
    ),
    ul: ({ children }) => (
      <Box as="ul" fontSize="14px" pl={5} mb={3} style={{ listStyle: 'disc' }} color="#343a40" lineHeight="1.75">
        {children}
      </Box>
    ),
    ol: ({ children }) => (
      <Box as="ol" fontSize="14px" pl={5} mb={3} style={{ listStyle: 'decimal' }} color="#343a40" lineHeight="1.75">
        {children}
      </Box>
    ),
    li: ({ children }) => <Box as="li" mb={1}>{children}</Box>,
    code: ({ children, className }) => {
      const inline = !className;
      if (inline) {
        return (
          <Box as="code" px={1.5} py={0.5} bg="#f8f9fa" color="#495057" borderRadius="4px" fontSize="13px" fontFamily="mono">
            {children}
          </Box>
        );
      }
      return (
        <Box as="pre" bg="#f8f9fa" p={4} borderRadius="8px" overflowX="auto" my={3}>
          <Box as="code" fontSize="13px" whiteSpace="pre" fontFamily="mono" color="#343a40">{children}</Box>
        </Box>
      );
    },
    blockquote: ({ children }) => (
      <Box pl={4} borderLeft="2px solid" borderColor="#e9ecef" color="#868e96" my={3}>
        {children}
      </Box>
    ),
    table: ({ children }) => (
      <Box as="table" fontSize="14px" my={4} w="100%" sx={{ borderCollapse: 'collapse' }}>
        {children}
      </Box>
    ),
    th: ({ children }) => (
      <Box as="th" borderBottom="2px solid" borderColor="#e9ecef" px={3} py={2} bg="#f8f9fa" fontWeight="600" textAlign="left" fontSize="13px" color="#495057">
        {children}
      </Box>
    ),
    td: ({ children }) => (
      <Box as="td" borderBottom="1px solid" borderColor="#f1f3f5" px={3} py={2} fontSize="14px">
        {children}
      </Box>
    ),
  };

  return (
    <Flex direction="column" h="100%" overflow="hidden" bg="white">
      <Flex
        px={6}
        py={4}
        borderBottom="1px solid"
        borderColor="#f1f3f5"
        align="center"
        gap={3}
        flexShrink={0}
      >
        <Box flex={1} minW={0}>
          <Flex align="center" gap={2} mb={0.5}>
            <Box
              px={2}
              py={0.5}
              borderRadius="5px"
              bg={catColors.bg}
              display="inline-flex"
            >
              <Text fontSize="11px" fontWeight="600" color={catColors.text}>
                {CATEGORY_LABELS[cat as keyof typeof CATEGORY_LABELS]}
              </Text>
            </Box>
            <Text fontSize="12px" color="#adb5bd">v{page.version}</Text>
          </Flex>
          <Text fontSize="20px" fontWeight="600" color="#212529" letterSpacing="-0.01em" noOfLines={1}>
            {pageName(page.path)}
          </Text>
        </Box>
        <Flex gap={1}>
          <Tooltip label="Surum gecmisi">
            <Button size="sm" variant="ghost" onClick={onOpenHistory} p={2}>
              <Clock size={14} strokeWidth={1.5} />
            </Button>
          </Tooltip>
          <Button size="sm" variant="ghost" onClick={onEdit} leftIcon={<Pencil size={13} strokeWidth={1.5} />}>
            Duzenle
          </Button>
          <Tooltip label="Sil">
            <Button size="sm" variant="ghost" onClick={onDelete} p={2} color="text.quaternary" _hover={{ color: 'status.failed' }}>
              <Trash2 size={14} strokeWidth={1.5} />
            </Button>
          </Tooltip>
        </Flex>
      </Flex>

      {page.broken_links.length > 0 && (
        <Box px={6} py={2} bg="#fff4e6" flexShrink={0}>
          <Text fontSize="12px" color="#b45309">
            Cozulemeyen baglar: {page.broken_links.join(', ')}
          </Text>
        </Box>
      )}

      <Box flex={1} overflowY="auto" px={6} py={6}>
        <Box maxW="720px" mx="auto">
          <ReactMarkdown components={components}>{rendered}</ReactMarkdown>

          {(page.backlinks.length > 0 || page.links.length > 0) && (
            <Box mt={10} pt={5} borderTop="1px solid" borderColor="#f1f3f5">
              {page.backlinks.length > 0 && (
                <VStack align="stretch" spacing={2} mb={4}>
                  <Text fontSize="12px" fontWeight="600" color="#868e96" textTransform="uppercase" letterSpacing="0.04em">
                    Baglanti veren sayfalar
                  </Text>
                  <HStack wrap="wrap" spacing={1.5}>
                    {page.backlinks.map((b) => (
                      <Box
                        key={b}
                        as="button"
                        px={2.5}
                        py={1}
                        bg="#f8f9fa"
                        borderRadius="6px"
                        fontSize="13px"
                        fontWeight="500"
                        color="#495057"
                        cursor="pointer"
                        _hover={{ bg: '#f1f3f5', shadow: '0 1px 3px rgba(0,0,0,0.04)' }}
                        transition="all 0.12s"
                        onClick={() => onNavigate(b)}
                      >
                        {b}
                      </Box>
                    ))}
                  </HStack>
                </VStack>
              )}

              {page.links.length > 0 && (
                <VStack align="stretch" spacing={2}>
                  <Text fontSize="12px" fontWeight="600" color="#868e96" textTransform="uppercase" letterSpacing="0.04em">
                    Disari baglantilar
                  </Text>
                  <HStack wrap="wrap" spacing={1.5}>
                    {page.links.map((l) => (
                      <Box
                        key={l}
                        px={2.5}
                        py={1}
                        bg="#f8f9fa"
                        borderRadius="6px"
                        fontSize="13px"
                        color="#868e96"
                      >
                        {l}
                      </Box>
                    ))}
                  </HStack>
                </VStack>
              )}
            </Box>
          )}
        </Box>
      </Box>
    </Flex>
  );
}
