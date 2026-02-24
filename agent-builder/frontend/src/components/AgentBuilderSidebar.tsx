/**
 * Agent Builder Sidebar Component
 * ================================
 *
 * Session listesi ve yeni session oluşturma.
 */

import React from 'react';
import {
  Box,
  VStack,
  HStack,
  Text,
  Button,
  Heading,
  useColorMode,
  IconButton,
  Divider,
  Badge,
} from '@chakra-ui/react';
import { AddIcon } from '@chakra-ui/icons';
import { BuilderSession } from '../../services/agentBuilderApi';

// =============================================================================
// TYPES
// =============================================================================

interface AgentBuilderSidebarProps {
  sessions: BuilderSession[];
  currentSessionId?: string;
  onSelectSession: (sessionId: string) => void;
  onNewSession: () => void;
  isLoading: boolean;
}

// =============================================================================
// COMPONENT
// =============================================================================

const AgentBuilderSidebar: React.FC<AgentBuilderSidebarProps> = ({
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  isLoading,
}) => {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  // State renkleri
  const getStateBadgeColor = (state: string) => {
    const colors: Record<string, string> = {
      goal_elicitation: 'blue',
      ontology_search: 'purple',
      skill_match: 'cyan',
      sample_request: 'orange',
      sample_analysis: 'yellow',
      schema_proposal: 'teal',
      schema_review: 'green',
      skill_generation: 'pink',
      agent_assembly: 'red',
      gateway_deploy: 'green',
    };
    return colors[state] || 'gray';
  };

  // Status renkleri
  const getStatusBadgeColor = (status: string) => {
    const colors: Record<string, string> = {
      active: 'green',
      completed: 'blue',
      abandoned: 'gray',
    };
    return colors[status] || 'gray';
  };

  return (
    <Box
      w="280px"
      h="100vh"
      bg={isDark ? 'gray.800' : 'white'}
      borderRight="1px"
      borderColor={isDark ? 'gray.700' : 'gray.200'}
      display="flex"
      flexDirection="column"
    >
      {/* Header */}
      <Box px={4} py={4} borderBottom="1px" borderColor={isDark ? 'gray.700' : 'gray.200'}>
        <HStack justify="space-between">
          <Heading size="sm" color={isDark ? 'white' : 'gray.800'}>
            Sessions
          </Heading>
          <IconButton
            aria-label="New Session"
            icon={<AddIcon />}
            size="sm"
            colorScheme="blue"
            onClick={onNewSession}
            isLoading={isLoading}
          />
        </HStack>
      </Box>

      {/* Session List */}
      <Box flex="1" overflowY="auto" px={2} py={2}>
        <VStack spacing={2} align="stretch">
          {sessions.length === 0 ? (
            <Box px={2} py={4} textAlign="center">
              <Text color={isDark ? 'gray.500' : 'gray.400'} fontSize="sm">
                Henüz session yok
              </Text>
              <Button
                mt={2}
                size="sm"
                colorScheme="blue"
                variant="ghost"
                onClick={onNewSession}
                isLoading={isLoading}
              >
                Yeni Başlat
              </Button>
            </Box>
          ) : (
            sessions.map((session) => (
              <Box
                key={session.id}
                px={3}
                py={3}
                borderRadius="md"
                cursor="pointer"
                bg={
                  session.id === currentSessionId
                    ? isDark
                      ? 'blue.800'
                      : 'blue.50'
                    : 'transparent'
                }
                _hover={{
                  bg: isDark ? 'gray.700' : 'gray.100',
                }}
                onClick={() => onSelectSession(session.id)}
                border="1px"
                borderColor={
                  session.id === currentSessionId
                    ? 'blue.500'
                    : 'transparent'
                }
              >
                {/* Session ID */}
                <Text
                  fontSize="sm"
                  fontWeight={session.id === currentSessionId ? 'bold' : 'normal'}
                  color={isDark ? 'white' : 'gray.800'}
                  noOfLines={1}
                >
                  {session.id}
                </Text>

                {/* Badges */}
                <HStack mt={1} spacing={1}>
                  <Badge
                    size="sm"
                    colorScheme={getStateBadgeColor(session.current_state)}
                    fontSize="2xs"
                  >
                    {session.current_state.replace(/_/g, ' ')}
                  </Badge>
                  <Badge
                    size="sm"
                    colorScheme={getStatusBadgeColor(session.status)}
                    fontSize="2xs"
                    variant="outline"
                  >
                    {session.status}
                  </Badge>
                </HStack>
              </Box>
            ))
          )}
        </VStack>
      </Box>

      {/* Footer */}
      <Box
        px={4}
        py={3}
        borderTop="1px"
        borderColor={isDark ? 'gray.700' : 'gray.200'}
      >
        <Text fontSize="xs" color={isDark ? 'gray.500' : 'gray.400'}>
          Agent Builder v0.1.0
        </Text>
      </Box>
    </Box>
  );
};

export default AgentBuilderSidebar;
