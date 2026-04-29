import { useState, useRef, useEffect, type FC } from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import {
  Box,
  Flex,
  Text,
  IconButton,
  Badge,
  Tooltip,
  Input,
  Popover,
  PopoverTrigger,
  PopoverContent,
  PopoverBody,
  Button,
  HStack,
  VStack,
  useToast,
} from '@chakra-ui/react';
import {
  Plus,
  MoreHorizontal,
  Trash2,
  Edit3,
  Copy,
  Share2,
  ChevronDown,
  Download,
  Check,
} from 'lucide-react';
import WorkflowCanvas from './Canvas';
import { useAgentContext } from '../../context/AgentContext';
import {
  shareWorkflowAsTemplate,
  listWorkflowTemplates,
  importWorkflowTemplate,
  type WorkflowTemplate,
} from '../../services/evolvingApi';

const STATUS_MAP: Record<string, { label: string; color: string; bg: string }> = {
  draft: { label: 'Taslak', color: '#64748b', bg: '#f1f5f9' },
  published: { label: 'Yayinda', color: '#059669', bg: '#ecfdf5' },
  archived: { label: 'Arsiv', color: '#d97706', bg: '#fffbeb' },
};

const WorkflowPanel: FC = () => {
  const {
    activeAgent,
    workflows,
    activeWorkflowId,
    selectWorkflow,
    createNewWorkflow,
    removeWorkflow,
    renameWorkflow,
    loadWorkflows,
  } = useAgentContext();

  const toast = useToast();
  const [showDropdown, setShowDropdown] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [showTemplates, setShowTemplates] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const agentId = activeAgent?.agent_id ?? '';
  const activeWf = workflows.find((w) => w.workflow_id === activeWorkflowId);
  const activeStatus = STATUS_MAP[activeWf?.status ?? 'draft'] ?? STATUS_MAP.draft;

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    if (showDropdown) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showDropdown]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    await createNewWorkflow(newName.trim());
    setNewName('');
    setShowCreate(false);
    setShowDropdown(false);
    toast({ title: `'${newName.trim()}' olusturuldu`, status: 'success', duration: 2000 });
  };

  const handleRename = async () => {
    if (!renamingId || !renameValue.trim()) return;
    await renameWorkflow(renamingId, renameValue.trim());
    setRenamingId(null);
    setRenameValue('');
  };

  const handleDelete = async (workflowId: string, name: string) => {
    if (workflows.length <= 1) {
      toast({ title: 'Son workflow silinemez', status: 'warning', duration: 2000 });
      return;
    }
    await removeWorkflow(workflowId);
    toast({ title: `'${name}' silindi`, status: 'info', duration: 2000 });
  };

  const handleShare = async (workflowId: string, name: string) => {
    if (!agentId) return;
    try {
      await shareWorkflowAsTemplate(agentId, workflowId);
      toast({ title: `'${name}' template olarak paylasildi`, status: 'success', duration: 2000 });
      await loadWorkflows();
    } catch {
      toast({ title: 'Paylasim hatasi', status: 'error', duration: 2000 });
    }
  };

  const handleShowTemplates = async () => {
    try {
      const resp = await listWorkflowTemplates(agentId);
      setTemplates(resp.data.templates || []);
      setShowTemplates(true);
    } catch {
      toast({ title: 'Template listesi alinamadi', status: 'error', duration: 2000 });
    }
  };

  const handleImportTemplate = async (t: WorkflowTemplate) => {
    if (!agentId) return;
    try {
      await importWorkflowTemplate(agentId, t.workflow_id);
      setShowTemplates(false);
      toast({ title: `'${t.name}' import edildi`, status: 'success', duration: 2000 });
    } catch {
      toast({ title: 'Import hatasi', status: 'error', duration: 2000 });
    }
  };

  return (
    <Box w="100%" h="100%" display="flex" flexDirection="column">
      {/* Workflow Header */}
      <Flex
        h="48px"
        flexShrink={0}
        borderBottom="1px solid"
        borderColor="border.subtle"
        bg="surface.primary"
        align="center"
        px={3}
        justify="space-between"
      >
        {/* Left: Workflow Selector */}
        <Flex align="center" gap={2}>
          <Box position="relative" ref={dropdownRef}>
            <Flex
              align="center"
              gap={2}
              px={3}
              py={1.5}
              borderRadius="10px"
              border="1px solid"
              borderColor={showDropdown ? '#93c5fd' : '#e2e8f0'}
              bg="white"
              cursor="pointer"
              _hover={{ borderColor: '#cbd5e1' }}
              transition="all 0.15s"
              onClick={() => setShowDropdown(!showDropdown)}
              shadow={showDropdown ? '0 0 0 3px rgba(59,130,246,0.08)' : 'none'}
            >
              <Text fontSize="13px" fontWeight="600" color="#1e293b" maxW="180px" noOfLines={1}>
                {activeWf?.name || 'Workflow Sec'}
              </Text>
              <Badge
                fontSize="9px"
                bg={activeStatus.bg}
                color={activeStatus.color}
                px={1.5}
                py={0.5}
                borderRadius="md"
                fontWeight="600"
              >
                v{activeWf?.version ?? 1}
              </Badge>
              <ChevronDown
                size={14}
                style={{
                  color: '#94a3b8',
                  transition: 'transform 0.15s',
                  transform: showDropdown ? 'rotate(180deg)' : 'rotate(0deg)',
                }}
              />
            </Flex>

            {/* Dropdown */}
            {showDropdown && (
              <Box
                position="absolute"
                top="calc(100% + 4px)"
                left={0}
                zIndex={40}
                bg="white"
                border="1px solid #e2e8f0"
                borderRadius="12px"
                shadow="0 10px 25px rgba(0,0,0,0.1), 0 4px 10px rgba(0,0,0,0.04)"
                w="320px"
                maxH="400px"
                overflow="hidden"
              >
                {/* Dropdown header */}
                <Flex
                  px={3}
                  py={2.5}
                  borderBottom="1px solid #f1f5f9"
                  align="center"
                  justify="space-between"
                >
                  <Text fontSize="11px" fontWeight="600" color="#94a3b8" textTransform="uppercase" letterSpacing="0.05em">
                    Workflow'lar ({workflows.length})
                  </Text>
                  <HStack spacing={1}>
                    <Tooltip label="Template Import" fontSize="xs">
                      <IconButton
                        icon={<Download size={13} />}
                        size="xs"
                        variant="ghost"
                        aria-label="Import"
                        color="#94a3b8"
                        _hover={{ color: '#3b82f6' }}
                        onClick={(e) => { e.stopPropagation(); handleShowTemplates(); }}
                      />
                    </Tooltip>
                    <Tooltip label="Yeni Workflow" fontSize="xs">
                      <IconButton
                        icon={<Plus size={13} />}
                        size="xs"
                        variant="ghost"
                        aria-label="Yeni"
                        color="#94a3b8"
                        _hover={{ color: '#3b82f6' }}
                        onClick={(e) => { e.stopPropagation(); setShowCreate(true); }}
                      />
                    </Tooltip>
                  </HStack>
                </Flex>

                {/* Create form */}
                {showCreate && (
                  <Box px={3} py={2.5} borderBottom="1px solid #f1f5f9" bg="#f8fafc">
                    <HStack>
                      <Input
                        size="sm"
                        placeholder="Workflow adi..."
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleCreate();
                          if (e.key === 'Escape') { setShowCreate(false); setNewName(''); }
                        }}
                        autoFocus
                        borderRadius="8px"
                        fontSize="13px"
                      />
                      <Button size="sm" colorScheme="blue" onClick={handleCreate} isDisabled={!newName.trim()} borderRadius="8px" fontSize="12px">
                        Olustur
                      </Button>
                    </HStack>
                  </Box>
                )}

                {/* Workflow list */}
                <VStack
                  spacing={0}
                  align="stretch"
                  maxH="280px"
                  overflowY="auto"
                  py={1}
                  sx={{
                    '&::-webkit-scrollbar': { width: '4px' },
                    '&::-webkit-scrollbar-thumb': { bg: '#e2e8f0', borderRadius: '2px' },
                  }}
                >
                  {workflows.map((wf) => {
                    const isActive = wf.workflow_id === activeWorkflowId;
                    const st = STATUS_MAP[wf.status] ?? STATUS_MAP.draft;
                    return (
                      <Flex
                        key={wf.workflow_id}
                        align="center"
                        px={3}
                        py={2}
                        mx={1}
                        borderRadius="8px"
                        cursor="pointer"
                        bg={isActive ? '#eff6ff' : 'transparent'}
                        _hover={{ bg: isActive ? '#eff6ff' : '#f8fafc' }}
                        transition="background 0.1s"
                        onClick={() => {
                          if (!isActive) selectWorkflow(wf.workflow_id);
                          setShowDropdown(false);
                        }}
                        role="group"
                      >
                        {isActive && (
                          <Box mr={2} flexShrink={0}>
                            <Check size={14} color="#3b82f6" strokeWidth={2.5} />
                          </Box>
                        )}
                        <Box flex={1} minW={0} ml={isActive ? 0 : 6}>
                          {renamingId === wf.workflow_id ? (
                            <Input
                              size="xs"
                              value={renameValue}
                              onChange={(e) => setRenameValue(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') handleRename();
                                if (e.key === 'Escape') setRenamingId(null);
                              }}
                              onBlur={handleRename}
                              autoFocus
                              onClick={(e) => e.stopPropagation()}
                              borderRadius="6px"
                            />
                          ) : (
                            <Flex align="center" gap={1.5}>
                              <Text
                                fontSize="13px"
                                fontWeight={isActive ? '600' : '500'}
                                color={isActive ? '#1e40af' : '#334155'}
                                noOfLines={1}
                              >
                                {wf.name}
                              </Text>
                              <Badge
                                fontSize="9px"
                                bg={st.bg}
                                color={st.color}
                                px={1.5}
                                borderRadius="md"
                                fontWeight="500"
                              >
                                v{wf.version}
                              </Badge>
                              {wf.is_template && (
                                <Badge fontSize="8px" colorScheme="purple" variant="outline" borderRadius="md">T</Badge>
                              )}
                            </Flex>
                          )}
                          <Text fontSize="11px" color="#94a3b8" mt={0.5}>
                            {wf.node_count} node &middot; {st.label}
                          </Text>
                        </Box>

                        {/* Actions */}
                        <HStack
                          spacing={0}
                          opacity={0}
                          _groupHover={{ opacity: 1 }}
                          transition="opacity 0.1s"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <Tooltip label="Yeniden adlandir" fontSize="xs">
                            <IconButton
                              icon={<Edit3 size={12} />}
                              size="xs"
                              variant="ghost"
                              aria-label="Rename"
                              color="#94a3b8"
                              _hover={{ color: '#3b82f6' }}
                              onClick={() => {
                                setRenamingId(wf.workflow_id);
                                setRenameValue(wf.name);
                              }}
                            />
                          </Tooltip>
                          <Tooltip label="Kopyala" fontSize="xs">
                            <IconButton
                              icon={<Copy size={12} />}
                              size="xs"
                              variant="ghost"
                              aria-label="Copy"
                              color="#94a3b8"
                              _hover={{ color: '#3b82f6' }}
                              onClick={() => createNewWorkflow(`${wf.name} (kopya)`)}
                            />
                          </Tooltip>
                          <Tooltip label="Template paylas" fontSize="xs">
                            <IconButton
                              icon={<Share2 size={12} />}
                              size="xs"
                              variant="ghost"
                              aria-label="Share"
                              color="#94a3b8"
                              _hover={{ color: '#3b82f6' }}
                              onClick={() => handleShare(wf.workflow_id, wf.name)}
                            />
                          </Tooltip>
                          {workflows.length > 1 && (
                            <Tooltip label="Sil" fontSize="xs">
                              <IconButton
                                icon={<Trash2 size={12} />}
                                size="xs"
                                variant="ghost"
                                aria-label="Delete"
                                color="#94a3b8"
                                _hover={{ color: '#ef4444' }}
                                onClick={() => handleDelete(wf.workflow_id, wf.name)}
                              />
                            </Tooltip>
                          )}
                        </HStack>
                      </Flex>
                    );
                  })}
                </VStack>
              </Box>
            )}

            {/* Template import popover */}
            {showTemplates && (
              <Box
                position="absolute"
                top="calc(100% + 4px)"
                left="0"
                zIndex={41}
                bg="white"
                border="1px solid #e2e8f0"
                borderRadius="12px"
                shadow="0 10px 25px rgba(0,0,0,0.1)"
                w="320px"
                maxH="300px"
                overflow="hidden"
              >
                <Flex px={3} py={2.5} borderBottom="1px solid #f1f5f9" align="center" justify="space-between">
                  <Text fontSize="11px" fontWeight="600" color="#94a3b8" textTransform="uppercase" letterSpacing="0.05em">
                    Template'ler
                  </Text>
                  <IconButton
                    icon={<MoreHorizontal size={12} />}
                    size="xs"
                    variant="ghost"
                    aria-label="Close"
                    onClick={() => setShowTemplates(false)}
                  />
                </Flex>
                <VStack spacing={0} align="stretch" maxH="240px" overflowY="auto" py={1}>
                  {templates.length === 0 ? (
                    <Text fontSize="12px" color="#94a3b8" py={4} textAlign="center">
                      Paylasilan template bulunamadi
                    </Text>
                  ) : (
                    templates.map((t) => (
                      <Flex
                        key={t.workflow_id}
                        align="center"
                        justify="space-between"
                        px={3}
                        py={2}
                        mx={1}
                        borderRadius="8px"
                        _hover={{ bg: '#f8fafc' }}
                        cursor="pointer"
                        onClick={() => handleImportTemplate(t)}
                      >
                        <Box>
                          <Text fontSize="13px" fontWeight="500" color="#334155">{t.name}</Text>
                          <Text fontSize="11px" color="#94a3b8">
                            {t.agent_name} &middot; {t.node_count} node &middot; v{t.version}
                          </Text>
                        </Box>
                        <Badge fontSize="10px" bg="#eff6ff" color="#3b82f6" px={2} borderRadius="md" fontWeight="500">
                          Import
                        </Badge>
                      </Flex>
                    ))
                  )}
                </VStack>
              </Box>
            )}
          </Box>

          <Text fontSize="11px" color="#94a3b8" fontWeight="500">
            {activeWf?.node_count ?? 0} node
          </Text>
        </Flex>
      </Flex>

      {/* Canvas */}
      <Box flex={1} position="relative">
        <ReactFlowProvider key={activeWorkflowId || 'default'}>
          <WorkflowCanvas workflowId={activeWorkflowId} />
        </ReactFlowProvider>
      </Box>
    </Box>
  );
};

export default WorkflowPanel;
