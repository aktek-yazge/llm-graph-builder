import { useMemo } from 'react';
import {
  Box, VStack, Text, HStack, Input, InputGroup, InputLeftElement,
  Accordion, AccordionItem, AccordionButton, AccordionPanel, AccordionIcon,
  Button, Tooltip,
} from '@chakra-ui/react';
import { Search, Plus } from 'lucide-react';
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
      w="280px"
      h="100%"
      bg="white"
      borderRight="1px solid"
      borderColor="#f1f3f5"
      spacing={0}
      flexShrink={0}
      overflow="hidden"
    >
      <Box p={3} borderBottom="1px solid" borderColor="#f1f3f5">
        <HStack>
          <InputGroup size="sm">
            <InputLeftElement pointerEvents="none">
              <Search size={13} color="#adb5bd" />
            </InputLeftElement>
            <Input
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              placeholder="Sayfalarda ara..."
              variant="filled"
              bg="#f8f9fa"
              border="none"
              borderRadius="8px"
              fontSize="13px"
              _hover={{ bg: '#f1f3f5' }}
              _focus={{ bg: '#f8f9fa', border: '1px solid', borderColor: '#dee2e6' }}
            />
          </InputGroup>
          <Tooltip label="Yeni sayfa">
            <Button
              size="sm"
              variant="ghost"
              onClick={onCreatePage}
              px={2}
            >
              <Plus size={15} strokeWidth={1.75} />
            </Button>
          </Tooltip>
        </HStack>
        <Button
          size="xs"
          mt={2}
          w="100%"
          variant={showingGraph ? 'solid' : 'ghost'}
          bg={showingGraph ? '#f1f3f5' : 'transparent'}
          color={showingGraph ? '#212529' : '#868e96'}
          _hover={{ bg: '#f1f3f5' }}
          fontWeight="500"
          fontSize="12px"
          onClick={onShowGraph}
        >
          {showingGraph ? 'Sayfaya don' : 'Graph goruntusu'}
        </Button>
      </Box>

      <Box flex={1} overflowY="auto" sx={{ '&::-webkit-scrollbar': { display: 'none' } }}>
        <Accordion defaultIndex={defaultIndex} allowMultiple reduceMotion>
          {CATEGORY_ORDER.map((cat) => {
            const items = grouped.get(cat)!;
            if (items.length === 0) return null;
            const catColors = CATEGORY_COLORS[cat];
            return (
              <AccordionItem key={cat} border="none">
                <AccordionButton
                  py={2}
                  px={3}
                  _hover={{ bg: '#f8f9fa' }}
                >
                  <HStack flex={1} spacing={2}>
                    <Box
                      w="6px"
                      h="6px"
                      bg={catColors.dot}
                      borderRadius="full"
                      flexShrink={0}
                    />
                    <Text fontSize="13px" fontWeight="600" color="#495057">
                      {CATEGORY_LABELS[cat]}
                    </Text>
                    <Text fontSize="11px" color="#adb5bd" fontWeight="500">
                      {items.length}
                    </Text>
                  </HStack>
                  <AccordionIcon color="#adb5bd" />
                </AccordionButton>
                <AccordionPanel p={0}>
                  <VStack align="stretch" spacing={0}>
                    {items.map((item) => {
                      const isActive = selectedPath === item.path;
                      return (
                        <Box
                          key={item.path}
                          px={3}
                          py={2}
                          ml={1}
                          cursor="pointer"
                          borderRadius="6px"
                          mx={1.5}
                          bg={isActive ? '#f1f3f5' : 'transparent'}
                          _hover={{ bg: isActive ? '#f1f3f5' : '#f8f9fa' }}
                          transition="background 0.1s"
                          onClick={() => onSelectPage(item.path)}
                          position="relative"
                        >
                          {isActive && (
                            <Box
                              position="absolute"
                              left="0"
                              top="6px"
                              bottom="6px"
                              w="2px"
                              borderRadius="full"
                              bg="#4c6ef5"
                            />
                          )}
                          <Text
                            fontSize="13px"
                            fontWeight={isActive ? '600' : '400'}
                            color={isActive ? '#212529' : '#495057'}
                            noOfLines={1}
                          >
                            {pageName(item.path)}
                          </Text>
                          {item.summary && (
                            <Text fontSize="11px" color="#adb5bd" noOfLines={1}>
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
            <Text fontSize="13px" color="#868e96">Henuz wiki sayfasi yok.</Text>
            <Text fontSize="12px" color="#adb5bd" mt={1}>
              Agent ile konusarak entity/relationship ekleyin; sayfalar otomatik
              olusacak.
            </Text>
          </Box>
        )}
      </Box>
    </VStack>
  );
}
