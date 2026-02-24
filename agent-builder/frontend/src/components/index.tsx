/**
 * Agent Builder Component
 * =======================
 *
 * Goal-driven agent oluşturma paneli.
 * Kullanıcıyla conversation state machine üzerinden etkileşim sağlar.
 *
 * Bileşenler:
 * - AgentBuilderChat: Conversation arayüzü
 * - AgentBuilderSidebar: Session ve agent listesi
 * - SampleUploader: Örnek belge yükleme
 * - SchemaViewer: Schema önerisi görüntüleme
 *
 * Kullanım:
 *   <AgentBuilder tenantId="tenant-001" />
 */

import React, { useState, useEffect, useCallback } from 'react';
import { Box, Flex, Heading, Text, Button, useColorMode } from '@chakra-ui/react';
import AgentBuilderChat from './AgentBuilderChat';
import AgentBuilderSidebar from './AgentBuilderSidebar';
import { agentBuilderApi, BuilderSession, BuilderChatResponse } from '../../services/agentBuilderApi';

// =============================================================================
// TYPES
// =============================================================================

interface AgentBuilderProps {
  tenantId: string;
}

// =============================================================================
// COMPONENT
// =============================================================================

const AgentBuilder: React.FC<AgentBuilderProps> = ({ tenantId }) => {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  // State
  const [currentSession, setCurrentSession] = useState<BuilderSession | null>(null);
  const [sessions, setSessions] = useState<BuilderSession[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ==========================================================================
  // SESSION MANAGEMENT
  // ==========================================================================

  /**
   * Yeni session oluştur
   */
  const createSession = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const session = await agentBuilderApi.createSession(tenantId);
      setCurrentSession(session);
      setSessions((prev) => [session, ...prev]);
    } catch (err) {
      setError('Session oluşturulamadı');
      console.error('Create session error:', err);
    } finally {
      setIsLoading(false);
    }
  }, [tenantId]);

  /**
   * Session seç
   */
  const selectSession = useCallback(async (sessionId: string) => {
    setIsLoading(true);
    setError(null);

    try {
      const session = await agentBuilderApi.getSession(sessionId);
      setCurrentSession(session);
    } catch (err) {
      setError('Session yüklenemedi');
      console.error('Get session error:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  /**
   * Mesaj gönder
   */
  const sendMessage = useCallback(
    async (message: string): Promise<BuilderChatResponse | null> => {
      if (!currentSession) return null;

      try {
        const response = await agentBuilderApi.sendMessage(currentSession.id, message);

        // Session state'ini güncelle
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                current_state: response.state,
                state_data: response.state_data || prev.state_data,
              }
            : null
        );

        return response;
      } catch (err) {
        console.error('Send message error:', err);
        return null;
      }
    },
    [currentSession]
  );

  // ==========================================================================
  // RENDER
  // ==========================================================================

  return (
    <Flex h="100vh" bg={isDark ? 'gray.900' : 'gray.50'}>
      {/* Sidebar */}
      <AgentBuilderSidebar
        sessions={sessions}
        currentSessionId={currentSession?.id}
        onSelectSession={selectSession}
        onNewSession={createSession}
        isLoading={isLoading}
      />

      {/* Main Content */}
      <Box flex="1" display="flex" flexDirection="column">
        {/* Header */}
        <Box
          px={6}
          py={4}
          borderBottom="1px"
          borderColor={isDark ? 'gray.700' : 'gray.200'}
          bg={isDark ? 'gray.800' : 'white'}
        >
          <Heading size="md" color={isDark ? 'white' : 'gray.800'}>
            Agent Builder
          </Heading>
          <Text fontSize="sm" color={isDark ? 'gray.400' : 'gray.600'}>
            {currentSession
              ? `Session: ${currentSession.id} | State: ${currentSession.current_state}`
              : 'Yeni bir session başlatın'}
          </Text>
        </Box>

        {/* Chat Area */}
        {currentSession ? (
          <AgentBuilderChat
            session={currentSession}
            onSendMessage={sendMessage}
            isLoading={isLoading}
          />
        ) : (
          <Flex flex="1" align="center" justify="center" direction="column" gap={4}>
            <Text color={isDark ? 'gray.400' : 'gray.600'}>
              Agent oluşturmak için yeni bir session başlatın.
            </Text>
            <Button colorScheme="blue" onClick={createSession} isLoading={isLoading}>
              Yeni Session Başlat
            </Button>
          </Flex>
        )}

        {/* Error Display */}
        {error && (
          <Box px={6} py={2} bg="red.500" color="white">
            <Text>{error}</Text>
          </Box>
        )}
      </Box>
    </Flex>
  );
};

export default AgentBuilder;
