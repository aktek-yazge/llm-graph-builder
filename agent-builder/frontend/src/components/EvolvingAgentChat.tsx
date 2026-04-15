import { useState, useRef, useEffect, useCallback } from 'react';
import {
  Box,
  Flex,
  Text,
  Input,
  IconButton,
  HStack,
  Tooltip,
  Badge,
  useColorMode,
} from '@chakra-ui/react';
import { ArrowForwardIcon, CloseIcon, EditIcon, AttachmentIcon, RepeatIcon } from '@chakra-ui/icons';
import ReactMarkdown from 'react-markdown';
import { useAgentContext, type ChatMessage } from '../context/AgentContext';

function MessageBubble({
  msg,
  onEdit,
  isStreaming,
}: {
  msg: ChatMessage;
  onEdit?: (id: string) => void;
  isStreaming: boolean;
}) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  if (msg.role === 'tool') {
    return (
      <Box
        mx={4}
        my={1}
        px={3}
        py={2}
        borderRadius="md"
        bg={isDark ? 'gray.800' : 'gray.100'}
        fontSize="xs"
        fontFamily="mono"
        borderLeft="2px solid"
        borderColor="orange.400"
      >
        <Text fontWeight="bold" color="orange.500">
          {msg.toolName || 'tool'}
        </Text>
        {msg.content && (
          <Box
            as="pre"
            mt={1}
            whiteSpace="pre-wrap"
            color={isDark ? 'gray.400' : 'gray.600'}
            maxH="32"
            overflowY="auto"
          >
            {msg.content.length > 500 ? msg.content.slice(0, 500) + '...' : msg.content}
          </Box>
        )}
      </Box>
    );
  }

  const isUser = msg.role === 'user';

  return (
    <Flex justify={isUser ? 'flex-end' : 'flex-start'} px={4} py={1} role="group">
      <Flex direction="column" alignItems={isUser ? 'flex-end' : 'flex-start'} maxW="80%">
        <Box
          borderRadius="xl"
          px={4}
          py={2}
          bg={isUser ? 'blue.500' : isDark ? 'gray.700' : 'gray.100'}
          color={isUser ? 'white' : isDark ? 'gray.100' : 'gray.900'}
        >
          {isUser ? (
            <Text>{msg.content}</Text>
          ) : (
            <Box className="prose prose-sm dark:prose-invert" sx={{ maxWidth: 'none' }}>
              <ReactMarkdown>{msg.content || '...'}</ReactMarkdown>
            </Box>
          )}
        </Box>
        {isUser && onEdit && !isStreaming && (
          <Tooltip label="Duzenle ve tekrar gonder" placement="left" fontSize="xs">
            <IconButton
              aria-label="Duzenle"
              icon={<EditIcon />}
              size="xs"
              variant="ghost"
              color="gray.400"
              _hover={{ color: 'blue.500' }}
              mt={0.5}
              opacity={0}
              _groupHover={{ opacity: 1 }}
              transition="opacity 0.15s"
              onClick={() => onEdit(msg.id)}
            />
          </Tooltip>
        )}
      </Flex>
    </Flex>
  );
}

export default function EvolvingAgentChat() {
  const { activeAgent, messages, isStreaming, sendMessage, editAndResend, uploadFiles, cancelStream, resetChat, mode, uploadedFiles, clearUploadedFiles } = useAgentContext();
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || !activeAgent) return;
    setInput('');
    sendMessage(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleEdit = (messageId: string) => {
    const text = editAndResend(messageId);
    if (text !== null) {
      setInput(text);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    uploadFiles(Array.from(files));
    e.target.value = '';
  };

  const [isDragOver, setIsDragOver] = useState(false);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);
      const droppedFiles = Array.from(e.dataTransfer.files);
      if (droppedFiles.length > 0) {
        uploadFiles(droppedFiles);
      }
    },
    [uploadFiles]
  );

  if (!activeAgent) {
    return (
      <Flex flex={1} align="center" justify="center" bg={isDark ? 'gray.900' : 'gray.50'}>
        <Box textAlign="center">
          <Text fontSize="lg" fontWeight="semibold" color="gray.400">
            Agent Secin
          </Text>
          <Text color="gray.400" mt={1}>
            Sol panelden bir agent secin veya yeni bir agent olusturun
          </Text>
        </Box>
      </Flex>
    );
  }

  return (
    <Flex
      flex={1}
      direction="column"
      bg={isDark ? 'gray.900' : 'white'}
      position="relative"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {isDragOver && (
        <Flex
          position="absolute"
          inset={0}
          zIndex={50}
          bg="rgba(59,130,246,0.12)"
          border="3px dashed"
          borderColor="blue.400"
          borderRadius="lg"
          align="center"
          justify="center"
          pointerEvents="none"
        >
          <Box textAlign="center">
            <Text fontSize="2xl" fontWeight="bold" color="blue.500">
              Dosyalari buraya birakin
            </Text>
            <Text fontSize="sm" color="blue.400" mt={1}>
              PDF, gorsel, CSV, DOCX vb.
            </Text>
          </Box>
        </Flex>
      )}
      {/* Header */}
      <Flex
        align="center"
        justify="space-between"
        px={4}
        py={3}
        borderBottom="1px"
        borderColor={isDark ? 'gray.700' : 'gray.200'}
      >
        <HStack spacing={3}>
          <Box>
            <Text fontWeight="semibold">{activeAgent.name}</Text>
            <Text fontSize="sm" color="gray.500">
              {activeAgent.domain || activeAgent.purpose || activeAgent.agent_id}
            </Text>
          </Box>
          <Badge
            colorScheme={mode === 'plan' ? 'yellow' : 'green'}
            fontSize="2xs"
            px={2}
            py={0.5}
            borderRadius="full"
          >
            {mode === 'plan' ? 'Plan' : 'Agent'}
          </Badge>
        </HStack>
        <HStack spacing={2}>
          {isStreaming && (
            <HStack spacing={1} color="blue.400" fontSize="xs">
              <Box w={2} h={2} bg="blue.400" borderRadius="full" className="animate-pulse" />
              <Text>Yanitliyor...</Text>
            </HStack>
          )}
          {messages.length > 0 && !isStreaming && (
            <Tooltip label="Yeni konusma baslat" placement="bottom" fontSize="xs">
              <IconButton
                aria-label="Yeni konusma"
                icon={<RepeatIcon />}
                size="xs"
                variant="ghost"
                color="gray.400"
                _hover={{ color: 'blue.400' }}
                onClick={resetChat}
              />
            </Tooltip>
          )}
        </HStack>
      </Flex>

      {/* Messages */}
      <Box ref={scrollRef} flex={1} overflowY="auto" py={4}>
        {messages.length === 0 && (
          <Flex align="center" justify="center" h="full" color="gray.400">
            <Box textAlign="center" maxW="md" px={4}>
              <Text fontSize="lg" fontWeight="medium" mb={3}>
                Agent ile konusmaya baslayin
              </Text>
              <Text fontSize="sm" mb={4}>
                Domain, entity ve relationship tanimlayabilirsiniz.
              </Text>
              <Box
                border="2px dashed"
                borderColor="gray.300"
                borderRadius="lg"
                py={6}
                px={4}
                cursor="pointer"
                _hover={{ borderColor: 'blue.300', bg: isDark ? 'gray.800' : 'gray.50' }}
                transition="all 0.2s"
                onClick={() => fileInputRef.current?.click()}
              >
                <Text fontSize="sm" fontWeight="medium" color="gray.500">
                  Dosyalari surukleyip birakin veya tiklayin
                </Text>
                <Text fontSize="xs" color="gray.400" mt={1}>
                  PDF, gorsel, CSV, DOCX &bull; S3/MinIO linklerini chat'e yapistirabilirsiniz
                </Text>
              </Box>
            </Box>
          </Flex>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} onEdit={handleEdit} isStreaming={isStreaming} />
        ))}
      </Box>

      {/* Uploaded Files Bar */}
      {uploadedFiles.length > 0 && (
        <Flex
          px={4}
          py={2}
          bg={isDark ? 'gray.800' : 'blue.50'}
          borderTop="1px"
          borderColor={isDark ? 'gray.700' : 'blue.100'}
          align="center"
          gap={2}
          flexWrap="wrap"
        >
          <AttachmentIcon color="blue.400" boxSize={3} />
          <Text fontSize="xs" color="blue.500" fontWeight="medium">
            {uploadedFiles.length} dosya yuklendi:
          </Text>
          {uploadedFiles.map((f, i) => (
            <Badge key={i} colorScheme="blue" fontSize="2xs" variant="subtle">
              {f.name}
            </Badge>
          ))}
          <Tooltip label="Dosya listesini temizle" fontSize="xs">
            <IconButton
              aria-label="Temizle"
              icon={<CloseIcon />}
              size="xs"
              variant="ghost"
              color="gray.400"
              onClick={clearUploadedFiles}
              ml="auto"
            />
          </Tooltip>
        </Flex>
      )}

      {/* Input */}
      <Box
        px={4}
        py={3}
        borderTop="1px"
        borderColor={isDark ? 'gray.700' : 'gray.200'}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.png,.jpg,.jpeg,.tiff,.docx,.xlsx,.csv,.txt"
          style={{ display: 'none' }}
          onChange={handleFileSelect}
        />
        <HStack>
          <Tooltip label="Dosya yukle (PDF, gorsel, vb.)" placement="top" fontSize="xs">
            <IconButton
              aria-label="Dosya yukle"
              icon={<AttachmentIcon />}
              size="sm"
              variant="ghost"
              color="gray.400"
              _hover={{ color: 'blue.500' }}
              onClick={() => fileInputRef.current?.click()}
              isDisabled={isStreaming}
            />
          </Tooltip>
          <Input
            ref={inputRef}
            flex={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Mesajinizi yazin veya S3/MinIO linki yapistirin..."
            isDisabled={isStreaming}
            bg={isDark ? 'gray.700' : 'gray.100'}
            border="none"
            _focus={{ boxShadow: 'outline' }}
          />
          {isStreaming ? (
            <IconButton
              aria-label="Durdur"
              icon={<CloseIcon />}
              onClick={cancelStream}
              colorScheme="red"
              variant="outline"
            />
          ) : (
            <IconButton
              aria-label="Gonder"
              icon={<ArrowForwardIcon />}
              onClick={handleSend}
              colorScheme="blue"
              isDisabled={!input.trim()}
            />
          )}
        </HStack>
      </Box>
    </Flex>
  );
}
