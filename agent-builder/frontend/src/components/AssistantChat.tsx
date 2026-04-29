import { useCallback, useRef, useState } from 'react';
import {
  Box,
  Flex,
  Text,
  HStack,
  Badge,
  IconButton,
  Tooltip,
  useColorMode,
} from '@chakra-ui/react';
import { CloseIcon } from '@chakra-ui/icons';
import { Paperclip, MessageSquarePlus, X } from 'lucide-react';
import { Thread } from '@assistant-ui/react-ui';
import '@assistant-ui/react-ui/styles/index.css';
import '@assistant-ui/react-ui/styles/tailwindcss/markdown.css';
import { useAgentContext } from '../context/AgentContext';
import { AgentRuntimeProvider } from '../runtime/AgentRuntimeProvider';
import PlanAwareText from './chat/PlanAwareText';
import {
  GenericToolCard,
  CreatePlanToolUI,
  UpdatePlanStepToolUI,
  AddPlanStepToolUI,
  RemovePlanStepToolUI,
  GetCurrentPlanToolUI,
  ListResourcesToolUI,
  DeleteResourceToolUI,
  DeleteAllResourcesToolUI,
  AddEntityClassToolUI,
  AddRelationshipPredicateToolUI,
  AddInferenceRuleToolUI,
  AddConstraintToolUI,
  GetCurrentOntologyToolUI,
  StartBatchProcessingToolUI,
  GetBatchProgressToolUI,
  RunOcrToolUI,
  OcrAndAnalyzeToolUI,
  RequestPlanModeToolUI,
  ToolStepsUI,
} from './chat/tools';

function ChatShell() {
  const {
    activeAgent,
    isStreaming,
    uploadFiles,
    resetChat,
    uploadedFiles,
    clearUploadedFiles,
    messages,
  } = useAgentContext();

  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    uploadFiles(Array.from(files));
    e.target.value = '';
  };

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
      const dropped = Array.from(e.dataTransfer.files);
      if (dropped.length > 0) uploadFiles(dropped);
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

  const hasMessages = messages.length > 0;

  return (
    <Flex
      flex={1}
      direction="column"
      h="100%"
      minH={0}
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
          bg="rgba(59,130,246,0.08)"
          border="2px dashed"
          borderColor="blue.300"
          borderRadius="xl"
          align="center"
          justify="center"
          pointerEvents="none"
        >
          <Box textAlign="center">
            <Text fontSize="lg" fontWeight="600" color="blue.500">
              Dosyalari buraya birakin
            </Text>
            <Text fontSize="xs" color="blue.400" mt={1}>
              PDF, gorsel, CSV, DOCX vb.
            </Text>
          </Box>
        </Flex>
      )}

      {isStreaming && (
        <Box flexShrink={0} px={4} pt={1.5} pb={0.5}>
          <HStack spacing={1.5} color="blue.400" fontSize="xs">
            <Box w={1.5} h={1.5} bg="blue.400" borderRadius="full" className="animate-pulse" />
            <Text fontWeight="500">Yanitliyor...</Text>
          </HStack>
        </Box>
      )}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.png,.jpg,.jpeg,.tiff,.docx,.xlsx,.csv,.txt"
        style={{ display: 'none' }}
        onChange={handleFileSelect}
      />

      {/* Thread */}
      <Box flex={1} minH={0} overflow="hidden" className={isDark ? 'aui-dark' : ''}>
        <Thread
          welcome={{
            message: 'Agent ile konusmaya baslayin. Domain, entity ve relationship tanimlayabilirsiniz.',
          }}
          assistantMessage={{
            allowReload: false,
            allowSpeak: false,
            allowFeedbackPositive: false,
            allowFeedbackNegative: false,
            components: {
              Text: PlanAwareText,
              ToolFallback: GenericToolCard,
            },
          }}
          tools={[
            ToolStepsUI,
            CreatePlanToolUI,
            UpdatePlanStepToolUI,
            AddPlanStepToolUI,
            RemovePlanStepToolUI,
            GetCurrentPlanToolUI,
            ListResourcesToolUI,
            DeleteResourceToolUI,
            DeleteAllResourcesToolUI,
            AddEntityClassToolUI,
            AddRelationshipPredicateToolUI,
            AddInferenceRuleToolUI,
            AddConstraintToolUI,
            GetCurrentOntologyToolUI,
            StartBatchProcessingToolUI,
            GetBatchProgressToolUI,
            RunOcrToolUI,
            OcrAndAnalyzeToolUI,
            RequestPlanModeToolUI,
          ]}
          strings={{
            welcome: { message: 'Agent ile konusmaya baslayin' },
            composer: {
              input: { placeholder: 'Mesajinizi yazin...' },
              send: { tooltip: 'Gonder' },
              cancel: { tooltip: 'Durdur' },
            },
          }}
        />
      </Box>

      {/* Bottom action strip */}
      <Flex
        align="center"
        px={4}
        py={1.5}
        flexShrink={0}
        gap={1}
        borderTop="1px"
        borderColor={isDark ? 'whiteAlpha.100' : 'gray.100'}
        bg={isDark ? 'gray.900' : 'gray.50'}
      >
        <Tooltip label="Dosya ekle" fontSize="xs" placement="top">
          <IconButton
            aria-label="Dosya ekle"
            icon={<Paperclip size={14} strokeWidth={1.5} />}
            size="xs"
            variant="ghost"
            color={isDark ? 'gray.500' : 'gray.400'}
            _hover={{ color: 'blue.500', bg: isDark ? 'whiteAlpha.100' : 'gray.200' }}
            borderRadius="md"
            onClick={() => fileInputRef.current?.click()}
            isDisabled={isStreaming}
          />
        </Tooltip>
        {hasMessages && !isStreaming && (
          <Tooltip label="Yeni sohbet" fontSize="xs" placement="top">
            <IconButton
              aria-label="Yeni sohbet"
              icon={<MessageSquarePlus size={14} strokeWidth={1.5} />}
              size="xs"
              variant="ghost"
              color={isDark ? 'gray.500' : 'gray.400'}
              _hover={{ color: 'blue.500', bg: isDark ? 'whiteAlpha.100' : 'gray.200' }}
              borderRadius="md"
              onClick={resetChat}
            />
          </Tooltip>
        )}

        {/* Uploaded files chips */}
        {uploadedFiles.length > 0 && (
          <>
            <Box w="1px" h="14px" bg={isDark ? 'whiteAlpha.200' : 'gray.200'} mx={1} />
            <HStack spacing={1} flex={1} flexWrap="wrap" overflow="hidden">
              {uploadedFiles.map((f, i) => (
                <Badge
                  key={i}
                  bg={isDark ? 'whiteAlpha.100' : 'blue.50'}
                  color={isDark ? 'blue.200' : 'blue.600'}
                  fontSize="10px"
                  fontWeight="500"
                  px={2}
                  py={0.5}
                  borderRadius="md"
                  display="flex"
                  alignItems="center"
                  gap={1}
                >
                  <Paperclip size={9} />
                  {f.name.length > 20 ? f.name.slice(0, 18) + '...' : f.name}
                </Badge>
              ))}
            </HStack>
            <Tooltip label="Temizle" fontSize="xs">
              <IconButton
                aria-label="Temizle"
                icon={<X size={12} strokeWidth={1.5} />}
                size="xs"
                variant="ghost"
                color="gray.400"
                _hover={{ color: 'red.400' }}
                borderRadius="md"
                onClick={clearUploadedFiles}
              />
            </Tooltip>
          </>
        )}
      </Flex>
    </Flex>
  );
}

export default function AssistantChat() {
  return (
    <AgentRuntimeProvider>
      <ChatShell />
    </AgentRuntimeProvider>
  );
}
