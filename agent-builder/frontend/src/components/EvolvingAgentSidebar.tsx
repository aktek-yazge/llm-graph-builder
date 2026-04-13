import { useEffect, useState } from 'react';
import {
  Box,
  Flex,
  Text,
  Input,
  Button,
  IconButton,
  VStack,
  useColorMode,
} from '@chakra-ui/react';
import { AddIcon, DeleteIcon } from '@chakra-ui/icons';
import { useAgentContext } from '../context/AgentContext';

export default function EvolvingAgentSidebar() {
  const { agents, activeAgent, loadAgents, selectAgent, createAgent, removeAgent } = useAgentContext();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newPurpose, setNewPurpose] = useState('');
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  useEffect(() => {
    loadAgents();
  }, [loadAgents]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    await createAgent(newName.trim(), newPurpose.trim());
    setNewName('');
    setNewPurpose('');
    setShowCreate(false);
  };

  const handleDelete = async (e: React.MouseEvent, agentId: string) => {
    e.stopPropagation();
    if (window.confirm('Bu agent silinecek. Emin misiniz?')) {
      await removeAgent(agentId);
    }
  };

  return (
    <Flex
      direction="column"
      h="full"
      w="64"
      flexShrink={0}
      borderRight="1px"
      borderColor={isDark ? 'gray.700' : 'gray.200'}
      bg={isDark ? 'gray.800' : 'white'}
    >
      {/* Header */}
      <Flex
        align="center"
        justify="space-between"
        p={4}
        borderBottom="1px"
        borderColor={isDark ? 'gray.700' : 'gray.200'}
      >
        <Text fontWeight="semibold" fontSize="md">
          Agent'lar
        </Text>
        <IconButton
          aria-label="Yeni agent"
          icon={<AddIcon />}
          size="sm"
          variant="ghost"
          onClick={() => setShowCreate(!showCreate)}
        />
      </Flex>

      {/* Create form */}
      {showCreate && (
        <Box p={3} borderBottom="1px" borderColor={isDark ? 'gray.700' : 'gray.200'}>
          <VStack spacing={2}>
            <Input
              size="sm"
              placeholder="orn: Sigorta Agent"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              autoFocus
            />
            <Input
              size="sm"
              placeholder="orn: Police belgelerini isle"
              value={newPurpose}
              onChange={(e) => setNewPurpose(e.target.value)}
            />
            <Flex gap={2} justify="flex-end" w="full">
              <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>
                Iptal
              </Button>
              <Button
                size="sm"
                colorScheme="blue"
                onClick={handleCreate}
                isDisabled={!newName.trim()}
              >
                Olustur
              </Button>
            </Flex>
          </VStack>
        </Box>
      )}

      {/* Agent list */}
      <Box flex={1} overflowY="auto">
        {agents.length === 0 && (
          <Box p={4} textAlign="center" color="gray.500">
            <Text fontSize="sm">Henuz agent yok. Yeni bir agent olusturun.</Text>
          </Box>
        )}
        {agents.map((agent) => (
          <Flex
            key={agent.agent_id}
            align="center"
            justify="space-between"
            px={4}
            py={3}
            cursor="pointer"
            _hover={{ bg: isDark ? 'gray.700' : 'gray.100' }}
            bg={
              activeAgent?.agent_id === agent.agent_id
                ? isDark
                  ? 'blue.900'
                  : 'blue.50'
                : 'transparent'
            }
            borderLeft={activeAgent?.agent_id === agent.agent_id ? '2px solid' : '2px solid transparent'}
            borderColor={activeAgent?.agent_id === agent.agent_id ? 'blue.500' : 'transparent'}
            onClick={() => selectAgent(agent.agent_id)}
          >
            <Box minW={0} flex={1}>
              <Text fontSize="sm" fontWeight="medium" isTruncated>
                {agent.name}
              </Text>
              <Text fontSize="xs" color="gray.500" isTruncated>
                {agent.is_empty
                  ? 'Ontoloji bos'
                  : `${agent.entity_count} entity, ${agent.relationship_count} rel`}
              </Text>
            </Box>
            <IconButton
              aria-label="Sil"
              icon={<DeleteIcon />}
              size="xs"
              variant="ghost"
              color="gray.400"
              _hover={{ color: 'red.500' }}
              onClick={(e) => handleDelete(e as React.MouseEvent, agent.agent_id)}
            />
          </Flex>
        ))}
      </Box>
    </Flex>
  );
}
