import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Flex,
  Text,
  Input,
  Button,
  IconButton,
  VStack,
  Tooltip,
  useColorMode,
  Collapse,
  Badge,
  Modal,
  ModalOverlay,
  ModalContent,
  ModalHeader,
  ModalCloseButton,
  ModalBody,
  ModalFooter,
  FormControl,
  FormLabel,
  Textarea,
  useDisclosure,
} from '@chakra-ui/react';
import {
  AddIcon,
  DeleteIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  RepeatIcon,
} from '@chakra-ui/icons';
import { useAgentContext } from '../context/AgentContext';

function formatDeletedAt(iso: string | null): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleString('tr-TR', { dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return iso;
  }
}

export default function EvolvingAgentSidebar() {
  const {
    agents,
    activeAgent,
    deletedAgents,
    loadAgents,
    selectAgent,
    createAgent,
    removeAgent,
    restoreAgent,
    purgeAgent,
    refreshDeletedAgents,
  } = useAgentContext();
  const navigate = useNavigate();
  const {
    isOpen: isCreateOpen,
    onOpen: openCreate,
    onClose: closeCreate,
  } = useDisclosure();
  const [newName, setNewName] = useState('');
  const [newPurpose, setNewPurpose] = useState('');
  const [creating, setCreating] = useState(false);
  const [trashOpen, setTrashOpen] = useState(false);
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  const resetCreateForm = () => {
    setNewName('');
    setNewPurpose('');
    setCreating(false);
  };

  const handleCloseCreate = () => {
    if (creating) return;
    resetCreateForm();
    closeCreate();
  };

  useEffect(() => {
    loadAgents();
    refreshDeletedAgents();
  }, [loadAgents, refreshDeletedAgents]);

  const handleSelect = async (agentId: string) => {
    await selectAgent(agentId);
    navigate(`/evolving/${agentId}`, { replace: true });
  };

  const handleSoftDelete = async (e: React.MouseEvent, agentId: string) => {
    e.stopPropagation();
    if (
      window.confirm(
        'Agent çöp kutusuna taşınacak. Tüm veriler korunur ve istediğin zaman geri yükleyebilirsin. Devam edilsin mi?'
      )
    ) {
      await removeAgent(agentId);
      setTrashOpen(true);
    }
  };

  const handleRestore = async (e: React.MouseEvent, agentId: string) => {
    e.stopPropagation();
    await restoreAgent(agentId);
  };

  const handlePurge = async (e: React.MouseEvent, agentId: string, name: string) => {
    e.stopPropagation();
    if (
      window.confirm(
        `"${name}" agent'ı KALICI olarak silinecek. Tüm dosyalar, OCR sonuçları, ontoloji ve wiki verileri geri getirilemez şekilde yok edilecek.\n\nDevam etmek istediğine emin misin?`
      )
    ) {
      await purgeAgent(agentId);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim() || creating) return;
    setCreating(true);
    try {
      const agent = await createAgent(newName.trim(), newPurpose.trim());
      resetCreateForm();
      closeCreate();
      navigate(`/evolving/${agent.agent_id}`, { replace: true });
    } catch (err) {
      setCreating(false);
      window.alert('Agent oluşturulamadı. Lütfen tekrar deneyin.');
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
        <Tooltip label="Yeni agent oluştur" hasArrow>
          <IconButton
            aria-label="Yeni agent"
            icon={<AddIcon />}
            size="sm"
            variant="ghost"
            onClick={() => {
              resetCreateForm();
              openCreate();
            }}
          />
        </Tooltip>
      </Flex>

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
            onClick={() => handleSelect(agent.agent_id)}
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
            <Tooltip label="Çöpe taşı (geri alınabilir)" hasArrow placement="left">
              <IconButton
                aria-label="Çöpe taşı"
                icon={<DeleteIcon />}
                size="xs"
                variant="ghost"
                color="gray.400"
                _hover={{ color: 'orange.500' }}
                onClick={(e) => handleSoftDelete(e as React.MouseEvent, agent.agent_id)}
              />
            </Tooltip>
          </Flex>
        ))}

        {/* Trash bin */}
        <Box
          mt={2}
          borderTop="1px"
          borderColor={isDark ? 'gray.700' : 'gray.200'}
        >
          <Flex
            align="center"
            justify="space-between"
            px={4}
            py={2}
            cursor="pointer"
            onClick={() => {
              setTrashOpen((v) => !v);
              if (!trashOpen) refreshDeletedAgents();
            }}
            _hover={{ bg: isDark ? 'gray.700' : 'gray.100' }}
          >
            <Flex align="center" gap={2}>
              {trashOpen ? <ChevronDownIcon /> : <ChevronRightIcon />}
              <Text fontSize="xs" fontWeight="semibold" color={isDark ? 'gray.300' : 'gray.600'}>
                Çöp Kutusu
              </Text>
              {deletedAgents.length > 0 && (
                <Badge colorScheme="orange" variant="subtle" fontSize="2xs">
                  {deletedAgents.length}
                </Badge>
              )}
            </Flex>
            <IconButton
              aria-label="Yenile"
              icon={<RepeatIcon />}
              size="xs"
              variant="ghost"
              onClick={(e) => {
                e.stopPropagation();
                refreshDeletedAgents();
              }}
            />
          </Flex>

          <Collapse in={trashOpen} animateOpacity>
            <Box pb={2}>
              {deletedAgents.length === 0 ? (
                <Box px={4} py={3}>
                  <Text fontSize="xs" color="gray.500">
                    Çöp boş.
                  </Text>
                </Box>
              ) : (
                deletedAgents.map((agent) => (
                  <Flex
                    key={agent.agent_id}
                    align="center"
                    justify="space-between"
                    px={4}
                    py={2}
                    _hover={{ bg: isDark ? 'gray.700' : 'gray.50' }}
                  >
                    <Box minW={0} flex={1}>
                      <Text fontSize="sm" fontWeight="medium" isTruncated color={isDark ? 'gray.300' : 'gray.700'}>
                        {agent.name}
                      </Text>
                      <Text fontSize="2xs" color="gray.500" isTruncated>
                        {formatDeletedAt(agent.deleted_at)}
                      </Text>
                    </Box>
                    <Flex gap={1}>
                      <Tooltip label="Geri yükle" hasArrow>
                        <IconButton
                          aria-label="Geri yükle"
                          icon={<RepeatIcon />}
                          size="xs"
                          variant="ghost"
                          color="green.500"
                          onClick={(e) => handleRestore(e as React.MouseEvent, agent.agent_id)}
                        />
                      </Tooltip>
                      <Tooltip label="Kalıcı sil" hasArrow>
                        <IconButton
                          aria-label="Kalıcı sil"
                          icon={<DeleteIcon />}
                          size="xs"
                          variant="ghost"
                          color="red.500"
                          onClick={(e) =>
                            handlePurge(e as React.MouseEvent, agent.agent_id, agent.name)
                          }
                        />
                      </Tooltip>
                    </Flex>
                  </Flex>
                ))
              )}
            </Box>
          </Collapse>
        </Box>
      </Box>

      {/* Create agent modal */}
      <Modal
        isOpen={isCreateOpen}
        onClose={handleCloseCreate}
        isCentered
        size="md"
        closeOnOverlayClick={!creating}
      >
        <ModalOverlay backdropFilter="blur(2px)" />
        <ModalContent bg={isDark ? 'gray.800' : 'white'}>
          <ModalHeader fontSize="md">Yeni Agent Oluştur</ModalHeader>
          <ModalCloseButton isDisabled={creating} />
          <ModalBody pb={4}>
            <VStack spacing={4} align="stretch">
              <FormControl isRequired>
                <FormLabel fontSize="sm">Ad</FormLabel>
                <Input
                  size="sm"
                  placeholder="örn: Sigorta Agent"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && newName.trim()) {
                      e.preventDefault();
                      handleCreate();
                    }
                  }}
                />
              </FormControl>
              <FormControl>
                <FormLabel fontSize="sm">Amaç (opsiyonel)</FormLabel>
                <Textarea
                  size="sm"
                  placeholder="örn: Poliçe belgelerini işle, müşteri risk profili çıkar"
                  value={newPurpose}
                  onChange={(e) => setNewPurpose(e.target.value)}
                  rows={3}
                  resize="vertical"
                />
                <Text fontSize="xs" color="gray.500" mt={1}>
                  Agent'ın domain'i ve ilk hedefi. Sonradan değiştirilebilir.
                </Text>
              </FormControl>
            </VStack>
          </ModalBody>
          <ModalFooter gap={2}>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleCloseCreate}
              isDisabled={creating}
            >
              İptal
            </Button>
            <Button
              size="sm"
              colorScheme="blue"
              onClick={handleCreate}
              isLoading={creating}
              loadingText="Oluşturuluyor"
              isDisabled={!newName.trim()}
            >
              Oluştur
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </Flex>
  );
}
