import { useState, useRef, useEffect } from 'react';
import {
  Box,
  Flex,
  Text,
  Input,
  IconButton,
  HStack,
  useColorMode,
} from '@chakra-ui/react';
import { ArrowForwardIcon, CloseIcon } from '@chakra-ui/icons';
import ReactMarkdown from 'react-markdown';
import { useAgentContext, type ChatMessage } from '../context/AgentContext';

function MessageBubble({ msg }: { msg: ChatMessage }) {
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
    <Flex justify={isUser ? 'flex-end' : 'flex-start'} px={4} py={1}>
      <Box
        maxW="80%"
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
    </Flex>
  );
}

export default function EvolvingAgentChat() {
  const { activeAgent, messages, isStreaming, sendMessage, cancelStream } = useAgentContext();
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
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
    <Flex flex={1} direction="column" bg={isDark ? 'gray.900' : 'white'}>
      {/* Header */}
      <Flex
        align="center"
        justify="space-between"
        px={4}
        py={3}
        borderBottom="1px"
        borderColor={isDark ? 'gray.700' : 'gray.200'}
      >
        <Box>
          <Text fontWeight="semibold">{activeAgent.name}</Text>
          <Text fontSize="sm" color="gray.500">
            {activeAgent.domain || activeAgent.purpose || activeAgent.agent_id}
          </Text>
        </Box>
        {isStreaming && (
          <HStack spacing={1} color="blue.400" fontSize="xs">
            <Box w={2} h={2} bg="blue.400" borderRadius="full" className="animate-pulse" />
            <Text>Yanitliyor...</Text>
          </HStack>
        )}
      </Flex>

      {/* Messages */}
      <Box ref={scrollRef} flex={1} overflowY="auto" py={4}>
        {messages.length === 0 && (
          <Flex align="center" justify="center" h="full" color="gray.400">
            <Text>Agent ile konusmaya baslayin. Domain, entity ve relationship tanimlayabilirsiniz.</Text>
          </Flex>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} />
        ))}
      </Box>

      {/* Input */}
      <Box
        px={4}
        py={3}
        borderTop="1px"
        borderColor={isDark ? 'gray.700' : 'gray.200'}
      >
        <HStack>
          <Input
            flex={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Mesajinizi yazin..."
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
