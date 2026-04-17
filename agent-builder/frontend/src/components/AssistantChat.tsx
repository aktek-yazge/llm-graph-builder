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
import { AttachmentIcon, CloseIcon, RepeatIcon } from '@chakra-ui/icons';
import { Thread } from '@assistant-ui/react-ui';
import '@assistant-ui/react-ui/styles/index.css';
import '@assistant-ui/react-ui/styles/markdown.css';
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
} from './chat/tools';

function ChatShell() {
  const {
    activeAgent,
    isStreaming,
    mode,
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
        flexShrink={0}
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
          <Tooltip label="Dosya yukle" fontSize="xs">
            <IconButton
              aria-label="Dosya yukle"
              icon={<AttachmentIcon />}
              size="xs"
              variant="ghost"
              color="gray.400"
              _hover={{ color: 'blue.400' }}
              onClick={() => fileInputRef.current?.click()}
              isDisabled={isStreaming}
            />
          </Tooltip>
          {hasMessages && !isStreaming && (
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

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.png,.jpg,.jpeg,.tiff,.docx,.xlsx,.csv,.txt"
        style={{ display: 'none' }}
        onChange={handleFileSelect}
      />

      {/* Assistant-UI Thread */}
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
              input: { placeholder: 'Mesajinizi yazin veya S3/MinIO linki yapistirin...' },
              send: { tooltip: 'Gonder' },
              cancel: { tooltip: 'Durdur' },
            },
          }}
        />
      </Box>

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
          flexShrink={0}
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
