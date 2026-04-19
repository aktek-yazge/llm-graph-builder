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
  Select,
  useDisclosure,
} from '@chakra-ui/react';
import { Plus, Trash2, ChevronDown, ChevronRight, RotateCcw } from 'lucide-react';
import { useAgentContext } from '../context/AgentContext';

const AVAILABLE_MODELS = [
  { provider: 'openai', model: 'gpt-5.4', label: 'GPT-5.4' },
  { provider: 'openai', model: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
  { provider: 'anthropic', model: 'claude-sonnet-4-6-20250414', label: 'Claude Sonnet 4.6' },
  { provider: 'anthropic', model: 'claude-opus-4-7-20250414', label: 'Claude Opus 4.7' },
  { provider: 'google', model: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
  { provider: 'google', model: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
];

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
  const [newModel, setNewModel] = useState('');
  const [creating, setCreating] = useState(false);
  const [trashOpen, setTrashOpen] = useState(false);

  const resetCreateForm = () => {
    setNewName('');
    setNewPurpose('');
    setNewModel('');
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
    navigate(`/agents/${agentId}`, { replace: true });
  };

  const handleSoftDelete = async (e: React.MouseEvent, agentId: string) => {
    e.stopPropagation();
    if (
      window.confirm(
        'Agent cop kutusuna tasinacak. Tum veriler korunur ve istedigin zaman geri yukleyebilirsin. Devam edilsin mi?'
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
        `"${name}" agent'i KALICI olarak silinecek. Tum dosyalar, OCR sonuclari, ontoloji ve wiki verileri geri getirilemez sekilde yok edilecek.\n\nDevam etmek istedigine emin misin?`
      )
    ) {
      await purgeAgent(agentId);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim() || creating) return;
    setCreating(true);
    try {
      const selected = AVAILABLE_MODELS.find(
        (m) => `${m.provider}/${m.model}` === newModel,
      );
      const agent = await createAgent(
        newName.trim(),
        newPurpose.trim(),
        selected?.provider,
        selected?.model,
      );
      resetCreateForm();
      closeCreate();
      navigate(`/agents/${agent.agent_id}`, { replace: true });
    } catch {
      setCreating(false);
      window.alert('Agent olusturulamadi. Lutfen tekrar deneyin.');
    }
  };

  return (
    <Flex
      direction="column"
      h="full"
      w="220px"
      flexShrink={0}
      borderRight="1px"
      borderColor="border.subtle"
      bg="surface.primary"
    >
      <Flex
        align="center"
        justify="space-between"
        px={4}
        py={3}
        borderBottom="1px"
        borderColor="border.subtle"
      >
        <Text fontWeight="600" fontSize="body" color="text.primary">
          Agent'lar
        </Text>
        <Tooltip label="Yeni agent olustur" hasArrow>
          <IconButton
            aria-label="Yeni agent"
            icon={<Plus size={14} strokeWidth={2} />}
            size="xs"
            variant="ghost"
            onClick={() => {
              resetCreateForm();
              openCreate();
            }}
          />
        </Tooltip>
      </Flex>

      <Box flex={1} overflowY="auto">
        {agents.length === 0 && (
          <Box p={4} textAlign="center">
            <Text fontSize="caption" color="text.tertiary">
              Henuz agent yok.
            </Text>
          </Box>
        )}
        {agents.map((agent) => {
          const isActive = activeAgent?.agent_id === agent.agent_id;
          return (
            <Flex
              key={agent.agent_id}
              align="center"
              justify="space-between"
              px={3}
              py={2.5}
              mx={1.5}
              my={0.5}
              cursor="pointer"
              borderRadius="8px"
              bg={isActive ? 'surface.tertiary' : 'transparent'}
              _hover={{ bg: isActive ? 'surface.tertiary' : 'surface.secondary' }}
              transition="background 0.12s"
              onClick={() => handleSelect(agent.agent_id)}
              position="relative"
            >
              {isActive && (
                <Box
                  position="absolute"
                  left="0"
                  top="8px"
                  bottom="8px"
                  w="2px"
                  borderRadius="full"
                  bg="brand.500"
                />
              )}
              <Box minW={0} flex={1}>
                <Text
                  fontSize="13px"
                  fontWeight={isActive ? '600' : '400'}
                  color={isActive ? 'text.primary' : 'text.secondary'}
                  noOfLines={1}
                >
                  {agent.name}
                </Text>
                <Text fontSize="11px" color="text.tertiary" noOfLines={1}>
                  {agent.is_empty
                    ? 'Ontoloji bos'
                    : `${agent.entity_count} entity, ${agent.relationship_count} rel`}
                </Text>
              </Box>
              <IconButton
                aria-label="Cope tasi"
                icon={<Trash2 size={12} strokeWidth={1.5} />}
                size="xs"
                variant="ghost"
                color="text.quaternary"
                _hover={{ color: 'status.failed' }}
                onClick={(e) => handleSoftDelete(e as React.MouseEvent, agent.agent_id)}
                opacity={0}
                _groupHover={{ opacity: 1 }}
                sx={{ '.chakra-flex:hover &': { opacity: 1 } }}
                transition="opacity 0.12s"
              />
            </Flex>
          );
        })}

        <Box mt={1} borderTop="1px" borderColor="border.subtle">
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
            _hover={{ bg: 'surface.secondary' }}
            transition="background 0.12s"
          >
            <Flex align="center" gap={1.5}>
              {trashOpen
                ? <ChevronDown size={12} strokeWidth={1.5} />
                : <ChevronRight size={12} strokeWidth={1.5} />}
              <Text fontSize="caption" fontWeight="500" color="text.tertiary">
                Cop Kutusu
              </Text>
              {deletedAgents.length > 0 && (
                <Badge
                  bg="surface.tertiary"
                  color="text.tertiary"
                  fontSize="micro"
                  borderRadius="full"
                  px={1.5}
                >
                  {deletedAgents.length}
                </Badge>
              )}
            </Flex>
            <IconButton
              aria-label="Yenile"
              icon={<RotateCcw size={11} strokeWidth={1.5} />}
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
                <Box px={4} py={2}>
                  <Text fontSize="caption" color="text.quaternary">Cop bos.</Text>
                </Box>
              ) : (
                deletedAgents.map((agent) => (
                  <Flex
                    key={agent.agent_id}
                    align="center"
                    justify="space-between"
                    px={4}
                    py={1.5}
                    _hover={{ bg: 'surface.secondary' }}
                  >
                    <Box minW={0} flex={1}>
                      <Text fontSize="caption" color="text.secondary" noOfLines={1}>
                        {agent.name}
                      </Text>
                      <Text fontSize="micro" color="text.quaternary" noOfLines={1}>
                        {formatDeletedAt(agent.deleted_at)}
                      </Text>
                    </Box>
                    <Flex gap={0.5}>
                      <Tooltip label="Geri yukle" hasArrow>
                        <IconButton
                          aria-label="Geri yukle"
                          icon={<RotateCcw size={11} strokeWidth={1.5} />}
                          size="xs"
                          variant="ghost"
                          color="status.completed"
                          onClick={(e) => handleRestore(e as React.MouseEvent, agent.agent_id)}
                        />
                      </Tooltip>
                      <Tooltip label="Kalici sil" hasArrow>
                        <IconButton
                          aria-label="Kalici sil"
                          icon={<Trash2 size={11} strokeWidth={1.5} />}
                          size="xs"
                          variant="ghost"
                          color="status.failed"
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

      <Modal
        isOpen={isCreateOpen}
        onClose={handleCloseCreate}
        isCentered
        size="md"
        closeOnOverlayClick={!creating}
      >
        <ModalOverlay />
        <ModalContent>
          <ModalHeader fontSize="subtitle" fontWeight="600">Yeni Agent Olustur</ModalHeader>
          <ModalCloseButton isDisabled={creating} />
          <ModalBody pb={4}>
            <VStack spacing={4} align="stretch">
              <FormControl isRequired>
                <FormLabel fontSize="caption" fontWeight="500" color="text.secondary">Ad</FormLabel>
                <Input
                  size="sm"
                  variant="filled"
                  placeholder="orn: Sigorta Agent"
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
                <FormLabel fontSize="caption" fontWeight="500" color="text.secondary">Amac (opsiyonel)</FormLabel>
                <Textarea
                  size="sm"
                  variant="filled"
                  placeholder="orn: Police belgelerini isle, musteri risk profili cikar"
                  value={newPurpose}
                  onChange={(e) => setNewPurpose(e.target.value)}
                  rows={3}
                  resize="vertical"
                  borderRadius="8px"
                />
                <Text fontSize="micro" color="text.quaternary" mt={1}>
                  Agent'in domain'i ve ilk hedefi. Sonradan degistirilebilir.
                </Text>
              </FormControl>
              <FormControl>
                <FormLabel fontSize="caption" fontWeight="500" color="text.secondary">LLM Model</FormLabel>
                <Select
                  size="sm"
                  variant="filled"
                  value={newModel}
                  onChange={(e) => setNewModel(e.target.value)}
                  borderRadius="8px"
                >
                  <option value="">Sistem Varsayilani</option>
                  {AVAILABLE_MODELS.map((m) => (
                    <option key={`${m.provider}/${m.model}`} value={`${m.provider}/${m.model}`}>
                      {m.label} ({m.provider})
                    </option>
                  ))}
                </Select>
                <Text fontSize="micro" color="text.quaternary" mt={1}>
                  Bos birakilirsa sistem varsayilani kullanilir. Sonradan degistirilebilir.
                </Text>
              </FormControl>
            </VStack>
          </ModalBody>
          <ModalFooter gap={2}>
            <Button variant="ghost" onClick={handleCloseCreate} isDisabled={creating}>
              Iptal
            </Button>
            <Button
              onClick={handleCreate}
              isLoading={creating}
              loadingText="Olusturuluyor"
              isDisabled={!newName.trim()}
            >
              Olustur
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </Flex>
  );
}
