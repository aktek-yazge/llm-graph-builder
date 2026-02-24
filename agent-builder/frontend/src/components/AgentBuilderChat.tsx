/**
 * Agent Builder Chat Component
 * ============================
 *
 * Conversation state machine ile etkileşim sağlayan chat arayüzü.
 * Mesaj gönderme, yanıt alma ve seçenek gösterimi.
 */

import React, { useState, useRef, useEffect } from 'react';
import {
  Box,
  Flex,
  Input,
  Button,
  VStack,
  HStack,
  Text,
  useColorMode,
  Spinner,
  Badge,
} from '@chakra-ui/react';
import { BuilderSession, BuilderChatResponse } from '../../services/agentBuilderApi';

// =============================================================================
// TYPES
// =============================================================================

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  options?: Array<{ id: string; label: string }>;
  action_required?: string;
}

interface AgentBuilderChatProps {
  session: BuilderSession;
  onSendMessage: (message: string) => Promise<BuilderChatResponse | null>;
  isLoading: boolean;
}

// =============================================================================
// COMPONENT
// =============================================================================

const AgentBuilderChat: React.FC<AgentBuilderChatProps> = ({
  session,
  onSendMessage,
  isLoading,
}) => {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  // State
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);

  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // ==========================================================================
  // EFFECTS
  // ==========================================================================

  // İlk yüklemede karşılama mesajı
  useEffect(() => {
    if (messages.length === 0) {
      handleSendMessage(''); // Boş mesaj - ilk karşılama
    }
  }, [session.id]);

  // Mesaj sonunda scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // ==========================================================================
  // HANDLERS
  // ==========================================================================

  const handleSendMessage = async (messageText: string) => {
    // User mesajını ekle (boş değilse)
    if (messageText.trim()) {
      const userMessage: Message = {
        id: `msg-${Date.now()}`,
        role: 'user',
        content: messageText,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, userMessage]);
    }

    setInputValue('');
    setIsSending(true);

    try {
      const response = await onSendMessage(messageText || '');

      if (response) {
        const assistantMessage: Message = {
          id: `msg-${Date.now()}-assistant`,
          role: 'assistant',
          content: response.message,
          timestamp: new Date(),
          options: response.options,
          action_required: response.action_required,
        };
        setMessages((prev) => [...prev, assistantMessage]);
      }
    } catch (err) {
      console.error('Send message error:', err);
    } finally {
      setIsSending(false);
      inputRef.current?.focus();
    }
  };

  const handleOptionClick = (optionId: string, optionLabel: string) => {
    handleSendMessage(optionLabel);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && !isSending) {
      e.preventDefault();
      handleSendMessage(inputValue);
    }
  };

  // ==========================================================================
  // RENDER
  // ==========================================================================

  return (
    <Flex flex="1" direction="column" overflow="hidden">
      {/* Messages Area */}
      <Box
        flex="1"
        overflowY="auto"
        px={6}
        py={4}
        bg={isDark ? 'gray.900' : 'gray.50'}
      >
        <VStack spacing={4} align="stretch">
          {messages.map((msg) => (
            <Box
              key={msg.id}
              alignSelf={msg.role === 'user' ? 'flex-end' : 'flex-start'}
              maxW="80%"
            >
              {/* Message Bubble */}
              <Box
                px={4}
                py={3}
                borderRadius="lg"
                bg={
                  msg.role === 'user'
                    ? 'blue.500'
                    : isDark
                    ? 'gray.700'
                    : 'white'
                }
                color={msg.role === 'user' ? 'white' : isDark ? 'white' : 'gray.800'}
                boxShadow="sm"
              >
                <Text whiteSpace="pre-wrap">{msg.content}</Text>

                {/* Action Required Badge */}
                {msg.action_required && (
                  <Badge mt={2} colorScheme="orange">
                    {msg.action_required}
                  </Badge>
                )}
              </Box>

              {/* Options */}
              {msg.options && msg.options.length > 0 && (
                <HStack mt={2} spacing={2} flexWrap="wrap">
                  {msg.options.map((option) => (
                    <Button
                      key={option.id}
                      size="sm"
                      variant="outline"
                      colorScheme="blue"
                      onClick={() => handleOptionClick(option.id, option.label)}
                      isDisabled={isSending}
                    >
                      {option.label}
                    </Button>
                  ))}
                </HStack>
              )}

              {/* Timestamp */}
              <Text
                fontSize="xs"
                color={isDark ? 'gray.500' : 'gray.400'}
                mt={1}
                textAlign={msg.role === 'user' ? 'right' : 'left'}
              >
                {msg.timestamp.toLocaleTimeString('tr-TR', {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </Text>
            </Box>
          ))}

          {/* Loading Indicator */}
          {isSending && (
            <Box alignSelf="flex-start" px={4} py={3}>
              <Spinner size="sm" color="blue.500" />
            </Box>
          )}

          <div ref={messagesEndRef} />
        </VStack>
      </Box>

      {/* Input Area */}
      <Box
        px={6}
        py={4}
        borderTop="1px"
        borderColor={isDark ? 'gray.700' : 'gray.200'}
        bg={isDark ? 'gray.800' : 'white'}
      >
        <HStack>
          <Input
            ref={inputRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Mesajınızı yazın..."
            isDisabled={isSending || isLoading}
            bg={isDark ? 'gray.700' : 'gray.100'}
            border="none"
            _focus={{ boxShadow: 'outline' }}
          />
          <Button
            colorScheme="blue"
            onClick={() => handleSendMessage(inputValue)}
            isLoading={isSending}
            isDisabled={!inputValue.trim() || isLoading}
          >
            Gönder
          </Button>
        </HStack>

        {/* Current State Display */}
        <Flex mt={2} justify="space-between" align="center">
          <Badge colorScheme="purple" fontSize="xs">
            State: {session.current_state}
          </Badge>
          <Text fontSize="xs" color={isDark ? 'gray.500' : 'gray.400'}>
            Session: {session.id}
          </Text>
        </Flex>
      </Box>
    </Flex>
  );
};

export default AgentBuilderChat;
