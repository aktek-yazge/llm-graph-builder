import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box, Flex, VStack, Text, IconButton, Spinner, useToast, Divider,
  AlertDialog, AlertDialogBody, AlertDialogFooter, AlertDialogHeader,
  AlertDialogContent, AlertDialogOverlay, Button, useDisclosure,
} from '@chakra-ui/react';
import { RepeatIcon } from '@chakra-ui/icons';

import WikiSidebar from './WikiSidebar';
import WikiPageView from './WikiPageView';
import WikiPageEditor from './WikiPageEditor';
import WikiGraph from './WikiGraph';
import WikiLintPanel from './WikiLintPanel';
import WikiSceneStats from './WikiSceneStats';
import ScenePreviewPanel from './ScenePreviewPanel';
import WikiHistoryDrawer from './WikiHistoryDrawer';

import {
  wikiListPages, wikiGetPage, wikiSavePage, wikiDeletePage,
  wikiSearch, wikiGraph as wikiGraphApi,
  type WikiPageSummary, type WikiPageDetail, type WikiGraphResponse,
} from '../../services/evolvingApi';

type Mode = 'view' | 'edit' | 'new' | 'graph';

interface WikiPanelProps {
  agentId: string;
}

export default function WikiPanel({ agentId }: WikiPanelProps) {
  const toast = useToast();

  const [pages, setPages] = useState<WikiPageSummary[]>([]);
  const [searchResults, setSearchResults] = useState<WikiPageSummary[] | null>(null);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<Mode>('view');
  const [currentPage, setCurrentPage] = useState<WikiPageDetail | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [graphData, setGraphData] = useState<WikiGraphResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [previewCats, setPreviewCats] = useState<string[]>([]);
  const [historyPath, setHistoryPath] = useState<string | null>(null);

  const previewDisc = useDisclosure();
  const historyDisc = useDisclosure();
  const deleteDisc = useDisclosure();
  const cancelRef = useRef<HTMLButtonElement>(null);

  const loadPages = useCallback(async () => {
    const res = await wikiListPages(agentId);
    setPages(res.data.pages);
  }, [agentId]);

  const loadPage = useCallback(
    async (path: string) => {
      setLoading(true);
      try {
        const res = await wikiGetPage(agentId, path);
        setCurrentPage(res.data);
        setMode('view');
      } catch {
        toast({ title: 'Sayfa bulunamadi', description: path, status: 'error', duration: 2500 });
        setCurrentPage(null);
      } finally {
        setLoading(false);
      }
    },
    [agentId, toast],
  );

  useEffect(() => { loadPages(); }, [loadPages]);

  useEffect(() => {
    if (selectedPath) {
      loadPage(selectedPath);
    } else {
      setCurrentPage(null);
      setMode('view');
    }
  }, [selectedPath, loadPage]);

  useEffect(() => {
    if (!query.trim()) { setSearchResults(null); return; }
    const handle = window.setTimeout(async () => {
      try {
        const res = await wikiSearch(agentId, query.trim());
        setSearchResults(
          res.data.results.map((r) => ({
            path: r.path, category: r.category, summary: r.summary,
            version: r.version, links: [], created_at: null,
          })),
        );
      } catch { setSearchResults([]); }
    }, 250);
    return () => window.clearTimeout(handle);
  }, [agentId, query]);

  const displayPages = searchResults ?? pages;

  const gotoPath = useCallback((path: string) => { setSelectedPath(path); }, []);

  const showGraph = useCallback(async () => {
    setMode('graph');
    setLoading(true);
    try {
      const res = await wikiGraphApi(agentId);
      setGraphData(res.data);
    } finally { setLoading(false); }
  }, [agentId]);

  const handleCreate = () => { setMode('new'); setCurrentPage(null); };
  const handleEdit = () => { if (currentPage) setMode('edit'); };

  const handleSave = async (path: string, content: string) => {
    try {
      await wikiSavePage(agentId, path, content);
      toast({ title: 'Sayfa kaydedildi', status: 'success', duration: 2000 });
      await loadPages();
      gotoPath(path);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Bilinmeyen hata';
      toast({ title: 'Kaydedilemedi', description: msg, status: 'error' });
    }
  };

  const handleDelete = async () => {
    if (!currentPage) return;
    try {
      await wikiDeletePage(agentId, currentPage.path);
      toast({ title: 'Sayfa silindi', status: 'success', duration: 2000 });
      await loadPages();
      setSelectedPath(null);
      setCurrentPage(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Bilinmeyen hata';
      toast({ title: 'Silinemedi', description: msg, status: 'error' });
    } finally { deleteDisc.onClose(); }
  };

  const openPreview = (cats: string[]) => { setPreviewCats(cats); previewDisc.onOpen(); };
  const openHistory = () => { if (!currentPage) return; setHistoryPath(currentPage.path); historyDisc.onOpen(); };

  const centerContent = useMemo(() => {
    if (mode === 'graph') {
      if (loading && !graphData) return <Flex h="100%" align="center" justify="center"><Spinner size="xl" /></Flex>;
      if (!graphData || graphData.nodes.length === 0) {
        return <Flex h="100%" align="center" justify="center" direction="column" gap={2}><Text color="gray.500">Henuz graph icin yeterli sayfa yok.</Text></Flex>;
      }
      return <WikiGraph graph={graphData} onSelectPage={gotoPath} />;
    }
    if (mode === 'edit' && currentPage) {
      return <WikiPageEditor initialPath={currentPage.path} initialContent={currentPage.content} isNew={false} onSave={handleSave} onCancel={() => setMode('view')} />;
    }
    if (mode === 'new') {
      return <WikiPageEditor initialPath="" initialContent={`# Yeni sayfa\n\nIcerik buraya...`} isNew onSave={handleSave} onCancel={() => { setMode('view'); }} />;
    }
    if (loading && !currentPage) return <Flex h="100%" align="center" justify="center"><Spinner size="lg" /></Flex>;
    if (currentPage) {
      return <WikiPageView page={currentPage} onEdit={handleEdit} onDelete={deleteDisc.onOpen} onNavigate={gotoPath} onOpenHistory={openHistory} />;
    }
    return (
      <Flex h="100%" align="center" justify="center" direction="column" gap={3} px={8}>
        <Text fontSize="lg" color="gray.500">Wiki (sahne)</Text>
        <Text fontSize="sm" color="gray.400" textAlign="center" maxW="md">
          Soldan bir sayfa secin, arama yapin ya da "Graph goruntusu" ile
          sayfalar arasi baglari inceleyin.
        </Text>
      </Flex>
    );
  }, [mode, loading, graphData, currentPage, gotoPath, handleSave, deleteDisc]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Flex direction="column" h="100%">
      <Flex h="40px" bg="white" borderBottom="1px solid" borderColor="gray.200" align="center" px={4} gap={3} flexShrink={0}>
        <Text fontSize="sm" fontWeight="bold">Wiki</Text>
        <Text fontSize="xs" color="gray.500">Sahne hazirligi (Pre-KG)</Text>
        <Box flex={1} />
        <IconButton
          aria-label="Refresh"
          icon={<RepeatIcon />}
          size="xs"
          variant="ghost"
          onClick={() => { loadPages(); if (selectedPath) loadPage(selectedPath); if (mode === 'graph') showGraph(); }}
        />
      </Flex>

      <Flex flex={1} overflow="hidden">
        <WikiSidebar
          pages={displayPages}
          selectedPath={selectedPath}
          query={query}
          onQueryChange={setQuery}
          onSelectPage={gotoPath}
          onCreatePage={handleCreate}
          onShowGraph={() => { if (mode === 'graph') { setMode('view'); if (currentPage) gotoPath(currentPage.path); } else { showGraph(); } }}
          showingGraph={mode === 'graph'}
        />

        <Box flex={1} minW={0} h="100%" bg="white" overflow="hidden">
          {centerContent}
        </Box>

        <VStack w="280px" h="100%" bg="white" borderLeft="1px solid" borderColor="gray.200" flexShrink={0} align="stretch" overflowY="auto" spacing={0}>
          <Box p={3}>
            <WikiSceneStats agentId={agentId} onPublished={() => {}} onPreview={openPreview} />
          </Box>
          <Divider />
          <Box p={3}>
            <WikiLintPanel agentId={agentId} onSelectPage={gotoPath} />
          </Box>
        </VStack>
      </Flex>

      <ScenePreviewPanel isOpen={previewDisc.isOpen} onClose={previewDisc.onClose} agentId={agentId} categories={previewCats} />
      <WikiHistoryDrawer isOpen={historyDisc.isOpen} onClose={historyDisc.onClose} agentId={agentId} path={historyPath} />

      <AlertDialog isOpen={deleteDisc.isOpen} leastDestructiveRef={cancelRef} onClose={deleteDisc.onClose}>
        <AlertDialogOverlay>
          <AlertDialogContent>
            <AlertDialogHeader fontSize="md" fontWeight="bold">Sayfayi sil</AlertDialogHeader>
            <AlertDialogBody fontSize="sm">{currentPage?.path} silinecek. Bu islem geri alinamaz.</AlertDialogBody>
            <AlertDialogFooter>
              <Button ref={cancelRef} onClick={deleteDisc.onClose} size="sm">Iptal</Button>
              <Button colorScheme="red" onClick={handleDelete} ml={3} size="sm">Sil</Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialogOverlay>
      </AlertDialog>
    </Flex>
  );
}
