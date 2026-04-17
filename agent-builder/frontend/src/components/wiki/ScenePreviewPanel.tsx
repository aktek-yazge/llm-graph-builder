import { useEffect, useMemo, useState } from 'react';
import {
  Modal, ModalOverlay, ModalContent, ModalHeader, ModalBody, ModalFooter, ModalCloseButton,
  Tabs, TabList, TabPanels, Tab, TabPanel,
  Box, Text, Button, HStack, Badge, Spinner, useToast, Tag,
} from '@chakra-ui/react';
import {
  getScenePreview, getScenePublished,
  type ScenePreview, type ScenePublished,
} from '../../services/evolvingApi';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  agentId: string;
  categories: string[];
}

function buildLineDiff(a: string, b: string): { kind: 'same' | 'add' | 'remove'; text: string }[] {
  const aLines = a.split('\n');
  const bLines = b.split('\n');

  const m = aLines.length;
  const n = bLines.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      if (aLines[i] === bLines[j]) dp[i][j] = dp[i + 1][j + 1] + 1;
      else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const out: { kind: 'same' | 'add' | 'remove'; text: string }[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (aLines[i] === bLines[j]) {
      out.push({ kind: 'same', text: aLines[i] });
      i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: 'remove', text: aLines[i] });
      i++;
    } else {
      out.push({ kind: 'add', text: bLines[j] });
      j++;
    }
  }
  while (i < m) { out.push({ kind: 'remove', text: aLines[i++] }); }
  while (j < n) { out.push({ kind: 'add', text: bLines[j++] }); }
  return out;
}

export default function ScenePreviewPanel({ isOpen, onClose, agentId, categories }: Props) {
  const [preview, setPreview] = useState<ScenePreview | null>(null);
  const [published, setPublished] = useState<ScenePublished | null>(null);
  const [loading, setLoading] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (!isOpen) return;
    const run = async () => {
      setLoading(true);
      try {
        const [pr, pub] = await Promise.all([
          getScenePreview(agentId, categories),
          getScenePublished(agentId),
        ]);
        setPreview(pr.data);
        setPublished(pub.data);
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Bilinmeyen hata';
        toast({ title: 'Sahne onizleme basarisiz', description: msg, status: 'error' });
      } finally {
        setLoading(false);
      }
    };
    run();
  }, [isOpen, agentId, categories, toast]);

  const diff = useMemo(() => {
    if (!preview || !published?.published) return [];
    return buildLineDiff(published.prompt || '', preview.prompt || '');
  }, [preview, published]);

  const copyCurrent = async () => {
    if (!preview) return;
    try {
      await navigator.clipboard.writeText(preview.prompt);
      toast({ title: 'Onizleme kopyalandi', status: 'success', duration: 2000 });
    } catch {
      toast({ title: 'Kopyalanamadi', status: 'error', duration: 2000 });
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="5xl" scrollBehavior="inside">
      <ModalOverlay />
      <ModalContent>
        <ModalHeader>
          <HStack>
            <Text>Sahne onizleme</Text>
            {preview && (
              <Badge colorScheme="purple">
                ~{preview.token_estimate.toLocaleString()} token
              </Badge>
            )}
            {preview && preview.filter_categories.length > 0 && (
              <HStack spacing={1}>
                {preview.filter_categories.map((c) => (
                  <Tag key={c} size="sm" colorScheme="blue">{c}</Tag>
                ))}
              </HStack>
            )}
          </HStack>
        </ModalHeader>
        <ModalCloseButton />

        <ModalBody>
          {loading ? (
            <HStack><Spinner /><Text>Yukleniyor...</Text></HStack>
          ) : (
            <Tabs variant="enclosed" size="sm">
              <TabList>
                <Tab>Onizleme (canli)</Tab>
                <Tab>Yayinda</Tab>
                <Tab>Fark</Tab>
              </TabList>
              <TabPanels>
                <TabPanel p={0} pt={3}>
                  {preview?.ontology_empty ? (
                    <Text color="gray.500" fontSize="sm">
                      Ontoloji bos. Sahne olusmasi icin once entity/relationship ekleyin.
                    </Text>
                  ) : (
                    <Box
                      as="pre"
                      fontSize="xs"
                      fontFamily="mono"
                      whiteSpace="pre-wrap"
                      bg="gray.50"
                      p={4}
                      borderRadius="md"
                      maxH="60vh"
                      overflowY="auto"
                    >
                      {preview?.prompt || ''}
                    </Box>
                  )}
                </TabPanel>
                <TabPanel p={0} pt={3}>
                  {!published?.published ? (
                    <Text color="gray.500" fontSize="sm">Henuz yayinlanmis sahne yok.</Text>
                  ) : (
                    <Box
                      as="pre"
                      fontSize="xs"
                      fontFamily="mono"
                      whiteSpace="pre-wrap"
                      bg="gray.50"
                      p={4}
                      borderRadius="md"
                      maxH="60vh"
                      overflowY="auto"
                    >
                      {published.prompt}
                    </Box>
                  )}
                </TabPanel>
                <TabPanel p={0} pt={3}>
                  {!published?.published ? (
                    <Text color="gray.500" fontSize="sm">
                      Yayinlanmis bir sahne olmadan fark hesaplanamaz.
                    </Text>
                  ) : (
                    <Box
                      fontSize="xs"
                      fontFamily="mono"
                      bg="gray.50"
                      p={4}
                      borderRadius="md"
                      maxH="60vh"
                      overflowY="auto"
                    >
                      {diff.map((line, idx) => (
                        <Box
                          key={idx}
                          bg={line.kind === 'add' ? 'green.100' : line.kind === 'remove' ? 'red.100' : 'transparent'}
                          color={line.kind === 'add' ? 'green.900' : line.kind === 'remove' ? 'red.900' : 'gray.800'}
                          px={1}
                          whiteSpace="pre-wrap"
                        >
                          <Box as="span" color="gray.500" mr={2}>
                            {line.kind === 'add' ? '+' : line.kind === 'remove' ? '-' : ' '}
                          </Box>
                          {line.text || ' '}
                        </Box>
                      ))}
                    </Box>
                  )}
                </TabPanel>
              </TabPanels>
            </Tabs>
          )}
        </ModalBody>

        <ModalFooter>
          <Button size="sm" variant="ghost" onClick={copyCurrent} isDisabled={!preview?.prompt}>
            Onizlemeyi kopyala
          </Button>
          <Button size="sm" colorScheme="gray" onClick={onClose} ml={2}>
            Kapat
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
