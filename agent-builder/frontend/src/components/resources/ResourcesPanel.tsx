import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Box,
  Flex,
  HStack,
  VStack,
  Text,
  Badge,
  Button,
  Spinner,
  useColorMode,
  useDisclosure,
  Icon,
  Modal,
  ModalOverlay,
  ModalContent,
  ModalHeader,
  ModalBody,
  ModalFooter,
  ModalCloseButton,
  Textarea,
  useToast,
} from '@chakra-ui/react';
import { ViewIcon, AttachmentIcon, RepeatIcon, AddIcon, LinkIcon } from '@chakra-ui/icons';
import { useAgentContext } from '../../context/AgentContext';
import { getResources, uploadFiles as apiUploadFiles, addSources, type ResourceFile } from '../../services/evolvingApi';
import OcrPreviewModal from './OcrPreviewModal';

// Bekleyen OCR varsa hizli polling, yoksa cok daha yavas (yeni dosya geldiginde
// SSE notification refresh tetikler, bu sadece guvenlik agi).
const POLL_INTERVAL_PENDING_MS = 8_000;
const POLL_INTERVAL_IDLE_MS = 60_000;

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function contentTypeLabel(ct: string): string {
  if (ct.includes('pdf')) return 'PDF';
  if (ct.includes('image')) return 'Image';
  if (ct.includes('word') || ct.includes('docx')) return 'Word';
  if (ct.includes('text')) return 'Text';
  return ct.split('/').pop() || ct;
}

export default function ResourcesPanel() {
  const { activeAgent, notifications } = useAgentContext();
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const toast = useToast();

  const [resources, setResources] = useState<ResourceFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');

  const previewModal = useDisclosure();
  const sourceModal = useDisclosure();
  const [previewDoc, setPreviewDoc] = useState<{
    docKey: string;
    fileName: string;
    totalPages: number;
  } | null>(null);
  const [sourceUrls, setSourceUrls] = useState('');

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const agentId = activeAgent?.agent_id;
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastNotifTsRef = useRef<number>(0);

  const fetchResources = useCallback(async () => {
    if (!agentId) return;
    try {
      const res = await getResources(agentId);
      setResources(res.data.resources);
      setError('');
    } catch {
      setError('Kaynaklar yuklenemedi');
    }
  }, [agentId]);

  // Bekleyen OCR durumunu bool olarak tut, polling effect bu primitive'e bagli
  // olsun ki her resources guncellemesinde interval yeniden olusturulmasin.
  const hasPending = resources.some((r) => r.ocr_status === 'pending');

  // Tek polling effect: bekleyen varsa hizli, yoksa idle interval. Cleanup
  // hep dogru calisir, cift interval olusmaz.
  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (!agentId) return;

    const interval = hasPending ? POLL_INTERVAL_PENDING_MS : POLL_INTERVAL_IDLE_MS;
    pollRef.current = setInterval(fetchResources, interval);
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [agentId, hasPending, fetchResources]);

  // Ilk yukleme + agent degisimi
  useEffect(() => {
    if (!agentId) return;
    setLoading(true);
    fetchResources().finally(() => setLoading(false));
  }, [agentId, fetchResources]);

  // SSE notification geldiginde event-driven refresh (polling beklemeden)
  useEffect(() => {
    if (!agentId || notifications.length === 0) return;
    const latest = notifications[0];
    const ts = typeof latest.timestamp === 'number' ? latest.timestamp : Date.now();
    if (ts <= lastNotifTsRef.current) return;
    lastNotifTsRef.current = ts;
    // Sadece dosya/OCR ile ilgili event tipleri icin tetikle
    const evt = String(latest.event_type || '');
    if (
      evt === 'document_complete' ||
      evt.includes('ocr') ||
      evt.includes('file') ||
      evt.includes('resource')
    ) {
      fetchResources();
    }
  }, [agentId, notifications, fetchResources]);

  const openPreview = (r: ResourceFile) => {
    if (!r.doc_key) return;
    setPreviewDoc({
      docKey: r.doc_key,
      fileName: r.filename,
      totalPages: r.page_count ?? 0,
    });
    previewModal.onOpen();
  };

  const handleFileUpload = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0 || !agentId) return;
    setUploading(true);
    try {
      await apiUploadFiles(agentId, Array.from(files));
      toast({
        title: `${files.length} dosya yuklendi`,
        status: 'success',
        duration: 3000,
        isClosable: true,
      });
      fetchResources();
    } catch (err) {
      toast({
        title: 'Yukleme hatasi',
        description: String(err),
        status: 'error',
        duration: 4000,
      });
    } finally {
      setUploading(false);
    }
  }, [agentId, toast, fetchResources]);

  const handleAddSources = useCallback(async () => {
    if (!agentId || !sourceUrls.trim()) return;
    const urls = sourceUrls
      .split('\n')
      .map((u) => u.trim())
      .filter(Boolean);
    if (urls.length === 0) return;

    const isS3 = urls.some((u) => u.startsWith('s3://') || u.startsWith('minio://'));
    const sourceType = isS3 ? 's3' : 'url';

    setUploading(true);
    try {
      await addSources(agentId, urls, sourceType);
      toast({
        title: `${urls.length} kaynak eklendi`,
        status: 'success',
        duration: 3000,
        isClosable: true,
      });
      setSourceUrls('');
      sourceModal.onClose();
      fetchResources();
    } catch (err) {
      toast({
        title: 'Kaynak ekleme hatasi',
        description: String(err),
        status: 'error',
        duration: 4000,
      });
    } finally {
      setUploading(false);
    }
  }, [agentId, sourceUrls, toast, fetchResources, sourceModal]);

  if (!agentId) {
    return (
      <Flex justify="center" align="center" h="100%" p={4}>
        <Text color="gray.500" fontSize="sm">Agent seciniz</Text>
      </Flex>
    );
  }

  return (
    <Box h="100%" overflowY="auto" p={3}>
      <Flex justify="space-between" align="center" mb={3}>
        <HStack spacing={2}>
          <Text fontSize="sm" fontWeight="bold" color={isDark ? 'gray.200' : 'gray.700'}>
            Kaynaklar
          </Text>
          <Badge colorScheme="gray" fontSize="2xs">{resources.length}</Badge>
        </HStack>
        <HStack spacing={1}>
          <Button
            size="xs"
            colorScheme="blue"
            leftIcon={<AddIcon />}
            onClick={() => fileInputRef.current?.click()}
            isLoading={uploading}
          >
            Dosya
          </Button>
          <Button
            size="xs"
            variant="outline"
            colorScheme="blue"
            onClick={() => folderInputRef.current?.click()}
            isLoading={uploading}
          >
            Klasor
          </Button>
          <Button
            size="xs"
            variant="outline"
            colorScheme="purple"
            leftIcon={<LinkIcon />}
            onClick={sourceModal.onOpen}
          >
            URL/S3
          </Button>
          <Button
            size="xs"
            variant="ghost"
            leftIcon={<RepeatIcon />}
            onClick={() => { setLoading(true); fetchResources().finally(() => setLoading(false)); }}
            isLoading={loading}
          >
            Yenile
          </Button>
        </HStack>
      </Flex>

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.png,.jpg,.jpeg,.tiff,.docx,.xlsx,.csv,.txt"
        style={{ display: 'none' }}
        onChange={(e) => handleFileUpload(e.target.files)}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        {...({ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
        style={{ display: 'none' }}
        onChange={(e) => handleFileUpload(e.target.files)}
      />

      {error && (
        <Text color="red.400" fontSize="xs" mb={2}>{error}</Text>
      )}

      {loading && resources.length === 0 ? (
        <Flex justify="center" py={8}>
          <Spinner size="sm" color="gray.400" />
        </Flex>
      ) : resources.length === 0 ? (
        <Text color="gray.500" fontSize="xs" textAlign="center" py={8}>
          Henuz dosya yuklenmemis.
        </Text>
      ) : (
        <VStack spacing={2} align="stretch">
          {resources.map((r) => (
            <Box
              key={r.resource_id || r.filename}
              p={3}
              borderRadius="lg"
              border="1px solid"
              borderColor={isDark ? 'gray.600' : 'gray.200'}
              bg={isDark ? 'gray.750' : 'white'}
              _hover={{ borderColor: isDark ? 'gray.500' : 'gray.300' }}
              transition="border-color 0.15s"
            >
              <HStack spacing={2} mb={1}>
                <Icon as={AttachmentIcon} boxSize={3} color={isDark ? 'gray.400' : 'gray.500'} />
                <Text fontSize="sm" fontWeight="medium" noOfLines={1} flex={1}>
                  {r.filename}
                </Text>
                <Badge
                  fontSize="2xs"
                  colorScheme={r.ocr_status === 'completed' ? 'green' : 'yellow'}
                  variant="subtle"
                >
                  {r.ocr_status === 'completed' ? 'OCR Tamam' : 'Bekliyor'}
                </Badge>
              </HStack>

              <HStack spacing={2} fontSize="2xs" color="gray.500" flexWrap="wrap">
                <Text>{formatSize(r.size)}</Text>
                <Text>{contentTypeLabel(r.content_type)}</Text>
                {r.page_count != null && (
                  <Text>{r.page_count} sayfa</Text>
                )}
                {r.total_chars != null && (
                  <Text>{r.total_chars.toLocaleString('tr-TR')} kar.</Text>
                )}
              </HStack>

              {r.ocr_status === 'completed' && r.doc_key && (
                <Button
                  mt={2}
                  size="xs"
                  colorScheme="orange"
                  variant="outline"
                  leftIcon={<ViewIcon />}
                  onClick={() => openPreview(r)}
                >
                  Onizle
                </Button>
              )}
            </Box>
          ))}
        </VStack>
      )}

      {previewDoc && agentId && (
        <OcrPreviewModal
          isOpen={previewModal.isOpen}
          onClose={previewModal.onClose}
          agentId={agentId}
          docKey={previewDoc.docKey}
          fileName={previewDoc.fileName}
          totalPages={previewDoc.totalPages}
        />
      )}

      <Modal isOpen={sourceModal.isOpen} onClose={sourceModal.onClose} size="md">
        <ModalOverlay />
        <ModalContent bg={isDark ? 'gray.800' : 'white'}>
          <ModalHeader fontSize="sm">Kaynak Ekle (URL / S3 / MinIO / Azure)</ModalHeader>
          <ModalCloseButton />
          <ModalBody>
            <Text fontSize="xs" color="gray.500" mb={2}>
              Her satira bir URL girin. S3 yollari icin s3://bucket/path, MinIO icin
              minio://bucket/path, Azure icin https://account.blob.core.windows.net/...
              formatini kullanin.
            </Text>
            <Textarea
              value={sourceUrls}
              onChange={(e) => setSourceUrls(e.target.value)}
              placeholder={'s3://my-bucket/documents/\nhttps://storage.blob.core.windows.net/...\nminio://ocr-bucket/reports/'}
              rows={5}
              fontSize="sm"
            />
          </ModalBody>
          <ModalFooter>
            <Button size="sm" variant="ghost" mr={2} onClick={sourceModal.onClose}>
              Iptal
            </Button>
            <Button
              size="sm"
              colorScheme="purple"
              onClick={handleAddSources}
              isLoading={uploading}
              isDisabled={!sourceUrls.trim()}
            >
              Ekle
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </Box>
  );
}
