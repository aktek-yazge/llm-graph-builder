import { useMemo } from 'react';
import {
  Box, VStack, Text, HStack, Badge, Input, InputGroup, InputLeftElement,
  Accordion, AccordionItem, AccordionButton, AccordionPanel, AccordionIcon,
  Button, Tooltip,
} from '@chakra-ui/react';
import { SearchIcon, AddIcon } from '@chakra-ui/icons';
import type { WikiCategory, WikiPageSummary } from '../../services/evolvingApi';
import {
  CATEGORY_LABELS, CATEGORY_ORDER, CATEGORY_COLORS, pageName,
} from './wikiUtils';

interface Props {
  pages: WikiPageSummary[];
  selectedPath: string | null;
  query: string;
  onQueryChange: (q: string) => void;
  onSelectPage: (path: string) => void;
  onCreatePage: () => void;
  onShowGraph: () => void;
  showingGraph: boolean;
}

export default function WikiSidebar({
  pages,
  selectedPath,
  query,
  onQueryChange,
  onSelectPage,
  onCreatePage,
  onShowGraph,
  showingGraph,
}: Props) {
  const grouped = useMemo(() => {
    const map = new Map<WikiCategory, WikiPageSummary[]>();
    for (const cat of CATEGORY_ORDER) map.set(cat, []);
    for (const p of pages) {
      const cat = (p.category in CATEGORY_LABELS ? p.category : 'general') as WikiCategory;
      map.get(cat)!.push(p);
    }
    for (const cat of CATEGORY_ORDER) {
      map.get(cat)!.sort((a, b) => a.path.localeCompare(b.path));
    }
    return map;
  }, [pages]);

  const defaultIndex = useMemo(
    () => CATEGORY_ORDER.map((_, i) => i).filter((i) => (grouped.get(CATEGORY_ORDER[i])!.length > 0)),
    [grouped]
  );

  return (
    <VStack
      align="stretch"
      w="300px"
      h="100%"
      bg="white"
      borderRight="1px solid"
      borderColor="gray.200"
      spacing={0}
      flexShrink={0}
      overflow="hidden"
    >
      <Box p={3} borderBottom="1px solid" borderColor="gray.100">
        <HStack>
          <InputGroup size="sm">
            <InputLeftElement pointerEvents="none">
              <SearchIcon color="gray.400" boxSize={3} />
            </InputLeftElement>
            <Input
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder="Sayfalarda ara..."
              borderRadius="md"
            />
          </InputGroup>
          <Tooltip label="Yeni sayfa">
            <Button size="sm" leftIcon={<AddIcon boxSize={2.5} />} onClick={onCreatePage}>
              Yeni
            </Button>
          </Tooltip>
        </HStack>
        <Button
          size="xs"
          mt={2}
          w="100%"
          variant={showingGraph ? 'solid' : 'outline'}
          colorScheme="purple"
          onClick={onShowGraph}
        >
          {showingGraph ? 'Sayfaya don' : 'Graph goruntusu'}
        </Button>
      </Box>

      <Box flex={1} overflowY="auto">
        <Accordion defaultIndex={defaultIndex} allowMultiple reduceMotion>
          {CATEGORY_ORDER.map((cat) => {
            const items = grouped.get(cat)!;
            if (items.length === 0) return null;
            return (
              <AccordionItem key={cat} border="none">
                <AccordionButton
                  py={2}
                  _hover={{ bg: 'gray.50' }}
                  _expanded={{ bg: 'gray.50' }}
                >
                  <HStack flex={1} spacing={2}>
                    <Box
                      w="8px"
                      h="8px"
                      bg={CATEGORY_COLORS[cat]}
                      borderRadius="full"
                    />
                    <Text fontSize="sm" fontWeight="semibold">
                      {CATEGORY_LABELS[cat]}
                    </Text>
                    <Badge fontSize="2xs" colorScheme="gray">
                      {items.length}
                    </Badge>
                  </HStack>
                  <AccordionIcon />
                </AccordionButton>
                <AccordionPanel p={0}>
                  <VStack align="stretch" spacing={0}>
                    {items.map((item) => {
                      const isActive = selectedPath === item.path;
                      return (
                        <Box
                          key={item.path}
                          px={4}
                          py={2}
                          cursor="pointer"
                          bg={isActive ? 'indigo.50' : 'transparent'}
                          borderLeft="3px solid"
                          borderLeftColor={isActive ? 'indigo.500' : 'transparent'}
                          _hover={{ bg: isActive ? 'indigo.50' : 'gray.50' }}
                          onClick={() => onSelectPage(item.path)}
                        >
                          <Text
                            fontSize="sm"
                            fontWeight={isActive ? 'semibold' : 'normal'}
                            color={isActive ? 'indigo.700' : 'gray.800'}
                            noOfLines={1}
                          >
                            {pageName(item.path)}
                          </Text>
                          {item.summary && (
                            <Text fontSize="xs" color="gray.500" noOfLines={1}>
                              {item.summary}
                            </Text>
                          )}
                        </Box>
                      );
                    })}
                  </VStack>
                </AccordionPanel>
              </AccordionItem>
            );
          })}
        </Accordion>
        {pages.length === 0 && (
          <Box p={6} textAlign="center">
            <Text fontSize="sm" color="gray.500">Henuz wiki sayfasi yok.</Text>
            <Text fontSize="xs" color="gray.400" mt={1}>
              Agent ile konusarak entity/relationship ekleyin; sayfalar otomatik
              olusacak.
            </Text>
          </Box>
        )}
      </Box>
    </VStack>
  );
}
