import { useState, useEffect } from 'react';
import {
  Box,
  Flex,
  HStack,
  Icon,
  Text,
  Badge,
  Spinner,
  Modal,
  ModalOverlay,
  ModalContent,
  ModalHeader,
  ModalBody,
  ModalCloseButton,
  useColorMode,
  IconButton,
  Tooltip,
} from '@chakra-ui/react';
import { ViewIcon, ChevronLeftIcon, ChevronRightIcon } from '@chakra-ui/icons';
import ReactMarkdown from 'react-markdown';
import { getOcrPages, type OcrPagesResponse } from '../../services/evolvingApi';

const PAGES_PER_VIEW = 3;

export interface OcrPreviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  agentId: string;
  docKey: string;
  fileName: string;
  totalPages: number;
}

export default function OcrPreviewModal({
  isOpen,
  onClose,
  agentId,
  docKey,
  fileName,
  totalPages,
}: OcrPreviewModalProps) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<OcrPagesResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!isOpen || !agentId || !docKey) return;
    setLoading(true);
    getOcrPages(agentId, docKey, offset, PAGES_PER_VIEW)
      .then((r) => setData(r.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [isOpen, agentId, docKey, offset]);

  useEffect(() => {
    if (!isOpen) setOffset(0);
  }, [isOpen]);

  const realTotal = data?.total_pages ?? totalPages;
  const currentEnd = Math.min(offset + PAGES_PER_VIEW, realTotal);

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="4xl" scrollBehavior="inside">
      <ModalOverlay />
      <ModalContent bg={isDark ? 'gray.800' : 'white'}>
        <ModalHeader fontSize="md" pb={1}>
          <HStack>
            <Icon as={ViewIcon} color="orange.500" />
            <Text>{fileName}</Text>
            <Badge colorScheme="gray" fontSize="2xs">
              {realTotal} sayfa
            </Badge>
          </HStack>
        </ModalHeader>
        <ModalCloseButton />
        <ModalBody pb={6}>
          {loading ? (
            <Flex justify="center" py={8}>
              <Spinner color="orange.400" />
            </Flex>
          ) : data && data.pages.length > 0 ? (
            <>
              {data.pages.map((pageText, idx) => (
                <Box
                  key={`${offset}-${idx}`}
                  mb={4}
                  p={4}
                  borderRadius="md"
                  bg={isDark ? 'gray.700' : 'gray.50'}
                  border="1px solid"
                  borderColor={isDark ? 'gray.600' : 'gray.200'}
                >
                  <Badge mb={2} colorScheme="orange" fontSize="2xs">
                    Sayfa {offset + idx + 1}
                  </Badge>
                  <Box
                    className="prose prose-sm dark:prose-invert"
                    sx={{ maxWidth: 'none' }}
                    fontSize="sm"
                  >
                    <ReactMarkdown>{pageText}</ReactMarkdown>
                  </Box>
                </Box>
              ))}
              <Flex justify="space-between" align="center" pt={2}>
                <Tooltip label="Onceki sayfalar">
                  <IconButton
                    aria-label="Onceki"
                    icon={<ChevronLeftIcon />}
                    size="sm"
                    onClick={() => setOffset(Math.max(0, offset - PAGES_PER_VIEW))}
                    isDisabled={offset === 0}
                  />
                </Tooltip>
                <Text fontSize="sm" color="gray.500">
                  {offset + 1}–{currentEnd} / {realTotal}
                </Text>
                <Tooltip label="Sonraki sayfalar">
                  <IconButton
                    aria-label="Sonraki"
                    icon={<ChevronRightIcon />}
                    size="sm"
                    onClick={() => setOffset(offset + PAGES_PER_VIEW)}
                    isDisabled={!data.has_more}
                  />
                </Tooltip>
              </Flex>
            </>
          ) : (
            <Text color="gray.500" textAlign="center" py={8}>
              Sayfa bulunamadi.
            </Text>
          )}
        </ModalBody>
      </ModalContent>
    </Modal>
  );
}
