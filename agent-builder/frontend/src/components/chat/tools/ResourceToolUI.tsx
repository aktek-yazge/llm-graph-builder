import { Box, Flex, HStack, Icon, Text, Badge, useColorMode } from '@chakra-ui/react';
import { AttachmentIcon, DeleteIcon, ExternalLinkIcon } from '@chakra-ui/icons';
import { makeAssistantToolUI } from '@assistant-ui/react';
import CompactToolWrapper from './CompactToolWrapper';

type ResourceItem = {
  name?: string;
  filename?: string;
  url?: string;
  size?: number;
  type?: string;
  resource_id?: string;
  path?: string;
};

type ParsedResources = {
  items: ResourceItem[];
  summary?: string;
  empty?: boolean;
};

function fromObject(obj: Record<string, unknown>): ParsedResources {
  const items: ResourceItem[] = [];
  const files = obj.files;
  const urls = obj.urls;
  const resources = obj.resources;
  const itemsField = obj.items;

  if (Array.isArray(files)) items.push(...(files as ResourceItem[]));
  if (Array.isArray(urls)) {
    for (const u of urls as unknown[]) {
      if (typeof u === 'string') items.push({ url: u, name: u });
      else if (u && typeof u === 'object') items.push(u as ResourceItem);
    }
  }
  if (Array.isArray(resources) && items.length === 0) items.push(...(resources as ResourceItem[]));
  if (Array.isArray(itemsField) && items.length === 0) items.push(...(itemsField as ResourceItem[]));

  return {
    items,
    summary: typeof obj.summary === 'string' ? (obj.summary as string) : undefined,
  };
}

function parseResources(result: unknown): ParsedResources {
  if (!result) return { items: [], empty: true };
  if (Array.isArray(result)) return { items: result as ResourceItem[] };
  if (typeof result === 'string') {
    const trimmed = result.trim();
    if (!trimmed) return { items: [], empty: true };
    if (trimmed.startsWith('[')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) return { items: parsed };
      } catch {
        /* fall through */
      }
    }
    if (trimmed.startsWith('{')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === 'object') {
          return fromObject(parsed as Record<string, unknown>);
        }
      } catch {
        /* fall through */
      }
    }
    // Plain text fallback ("Henuz dosya yok" gibi mesajlar tek mesaj olarak gosterilir).
    return { items: [], summary: trimmed, empty: true };
  }
  if (typeof result === 'object' && result != null) {
    return fromObject(result as Record<string, unknown>);
  }
  return { items: [], empty: true };
}

function formatSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function ResourceListCard({ result }: { result: unknown }) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const { items, summary, empty: parseEmpty } = parseResources(result);
  const empty = items.length === 0;

  const summaryText = empty
    ? summary || 'kaynak yok'
    : summary || `${items.length} kaynak`;

  return (
    <CompactToolWrapper
      toolName="Kaynaklar"
      icon={AttachmentIcon}
      colorScheme="teal"
      summary={summaryText}
      badges={
        !empty && (
          <Badge colorScheme="teal" variant="subtle" fontSize="2xs">
            {items.length}
          </Badge>
        )
      }
    >
      {empty && parseEmpty && summary && (
        <Box px={3} py={2}>
          <Text fontSize="xs" color={isDark ? 'gray.400' : 'gray.500'} fontStyle="italic">
            {summary}
          </Text>
        </Box>
      )}
      {!empty && (
        <Box px={3} py={2}>
          <Flex direction="column" gap={1}>
            {items.slice(0, 20).map((item, idx) => {
              const isUrl = !!item.url;
              const label = item.name || item.filename || item.url || `resource-${idx}`;
              const sizeStr = formatSize(item.size);
              return (
                <HStack
                  key={`${label}-${idx}`}
                  spacing={2}
                  px={2}
                  py={1}
                  borderRadius="md"
                  _hover={{ bg: isDark ? 'gray.700' : 'gray.50' }}
                >
                  <Icon
                    as={isUrl ? ExternalLinkIcon : AttachmentIcon}
                    boxSize={3}
                    color={isUrl ? 'blue.400' : 'teal.400'}
                  />
                  <Text fontSize="xs" color={isDark ? 'gray.100' : 'gray.800'} noOfLines={1} flex={1}>
                    {label}
                  </Text>
                  {sizeStr && (
                    <Text fontSize="2xs" color="gray.500" flexShrink={0}>
                      {sizeStr}
                    </Text>
                  )}
                  {item.type && (
                    <Badge fontSize="2xs" colorScheme="gray" variant="subtle">
                      {item.type}
                    </Badge>
                  )}
                </HStack>
              );
            })}
            {items.length > 20 && (
              <Text fontSize="2xs" color="gray.500" mt={1}>
                +{items.length - 20} dosya daha
              </Text>
            )}
          </Flex>
        </Box>
      )}
    </CompactToolWrapper>
  );
}

function ResourceDeletedCard({
  label,
  result,
  args,
}: {
  label: string;
  result: unknown;
  args?: { filename?: string; url?: string };
}) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const target = args?.filename || args?.url || 'kaynak';
  const message = typeof result === 'string' ? result : result ? JSON.stringify(result) : '';
  const showDetails = message && message !== target;

  return (
    <CompactToolWrapper
      toolName={label}
      icon={DeleteIcon}
      colorScheme="red"
      summary={target}
    >
      {showDetails && (
        <Box px={3} py={2}>
          <Text fontSize="2xs" color={isDark ? 'red.300' : 'red.500'} fontStyle="italic">
            {message.length > 400 ? message.slice(0, 400) + '…' : message}
          </Text>
        </Box>
      )}
    </CompactToolWrapper>
  );
}

export const ListResourcesToolUI = makeAssistantToolUI<Record<string, unknown>, unknown>({
  toolName: 'list_resources',
  render: ({ result }) => <ResourceListCard result={result} />,
});

export const DeleteResourceToolUI = makeAssistantToolUI<{ filename?: string; url?: string }, unknown>({
  toolName: 'delete_resource',
  render: ({ result, args }) => <ResourceDeletedCard label="Kaynak silindi" result={result} args={args} />,
});

export const DeleteAllResourcesToolUI = makeAssistantToolUI<{ confirm?: boolean }, unknown>({
  toolName: 'delete_all_resources',
  render: ({ result }) => (
    <ResourceDeletedCard label="Tum kaynaklar silindi" result={result} args={{ filename: 'Butun kaynaklar' }} />
  ),
});
