import { useState, type FC } from 'react';
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
  Menu,
  MenuButton,
  MenuList,
  MenuItem,
  useToast,
} from '@chakra-ui/react';
import { Plus, MoreHorizontal, Trash2, Edit3, Copy, Share2 } from 'lucide-react';
import WorkflowCanvas from './Canvas';
import { useAgentContext } from '../../context/AgentContext';
import {
  shareWorkflowAsTemplate,
  listWorkflowTemplates,
  importWorkflowTemplate,
  type WorkflowTemplate,
} from '../../services/evolvingApi';

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
  const [newName, setNewName] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [showTemplates, setShowTemplates] = useState(false);

  const agentId = activeAgent?.agent_id ?? '';

  const handleCreate = async () => {
    if (!newName.trim()) return;
    await createNewWorkflow(newName.trim());
    setNewName('');
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

  const statusColor: Record<string, string> = {
    draft: 'gray',
    published: 'green',
    archived: 'orange',
  };

  return (
    <Box w="100%" h="100%" display="flex" flexDirection="column">
      {/* Workflow Tab Bar */}
      <Flex
        h="42px"
        flexShrink={0}
        borderBottom="1px solid"
        borderColor="border.subtle"
        bg="surface.primary"
        align="center"
        px={2}
        gap={1}
        overflowX="auto"
        sx={{ '&::-webkit-scrollbar': { height: '3px' }, '&::-webkit-scrollbar-thumb': { bg: 'gray.300', borderRadius: '2px' } }}
      >
        {workflows.map((wf) => {
          const isActive = wf.workflow_id === activeWorkflowId;
          return (
            <Flex
              key={wf.workflow_id}
              align="center"
              gap={1}
              px={2.5}
              py={1}
              cursor="pointer"
              borderRadius="6px"
              bg={isActive ? 'white' : 'transparent'}
              border="1px solid"
              borderColor={isActive ? '#e9ecef' : 'transparent'}
              shadow={isActive ? '0 1px 3px rgba(0,0,0,0.04)' : 'none'}
              _hover={{ bg: isActive ? 'white' : 'gray.50' }}
              onClick={() => !isActive && selectWorkflow(wf.workflow_id)}
              flexShrink={0}
            >
              {renamingId === wf.workflow_id ? (
                <Input
                  size="xs"
                  w="120px"
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleRename();
                    if (e.key === 'Escape') setRenamingId(null);
                  }}
                  onBlur={handleRename}
                  autoFocus
                />
              ) : (
                <>
                  <Text
                    fontSize="12px"
                    fontWeight={isActive ? '600' : '500'}
                    color={isActive ? '#212529' : '#868e96'}
                    noOfLines={1}
                    maxW="140px"
                  >
                    {wf.name}
                  </Text>
                  <Badge
                    fontSize="9px"
                    colorScheme={statusColor[wf.status] || 'gray'}
                    variant="subtle"
                    textTransform="uppercase"
                  >
                    v{wf.version}
                  </Badge>
                  {wf.is_template && (
                    <Badge fontSize="8px" colorScheme="purple" variant="outline">T</Badge>
                  )}
                </>
              )}

              {isActive && renamingId !== wf.workflow_id && (
                <Menu>
                  <MenuButton
                    as={IconButton}
                    icon={<MoreHorizontal size={12} />}
                    size="xs"
                    variant="ghost"
                    minW="20px"
                    h="20px"
                    aria-label="Workflow menu"
                    onClick={(e) => e.stopPropagation()}
                  />
                  <MenuList minW="160px" fontSize="13px">
                    <MenuItem
                      icon={<Edit3 size={14} />}
                      onClick={(e) => {
                        e.stopPropagation();
                        setRenamingId(wf.workflow_id);
                        setRenameValue(wf.name);
                      }}
                    >
                      Yeniden Adlandir
                    </MenuItem>
                    <MenuItem
                      icon={<Copy size={14} />}
                      onClick={(e) => {
                        e.stopPropagation();
                        createNewWorkflow(`${wf.name} (kopya)`);
                      }}
                    >
                      Kopyala
                    </MenuItem>
                    <MenuItem
                      icon={<Share2 size={14} />}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleShare(wf.workflow_id, wf.name);
                      }}
                    >
                      Template Olarak Paylas
                    </MenuItem>
                    <MenuItem
                      icon={<Trash2 size={14} />}
                      color="red.500"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(wf.workflow_id, wf.name);
                      }}
                    >
                      Sil
                    </MenuItem>
                  </MenuList>
                </Menu>
              )}
            </Flex>
          );
        })}

        {/* New workflow button */}
        <Popover placement="bottom-start">
          <PopoverTrigger>
            <Tooltip label="Yeni Workflow" fontSize="xs">
              <IconButton
                icon={<Plus size={14} />}
                size="xs"
                variant="ghost"
                aria-label="Yeni Workflow"
                flexShrink={0}
              />
            </Tooltip>
          </PopoverTrigger>
          <PopoverContent w="240px">
            <PopoverBody>
              <HStack>
                <Input
                  size="sm"
                  placeholder="Workflow ismi..."
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                />
                <Button size="sm" colorScheme="blue" onClick={handleCreate} isDisabled={!newName.trim()}>
                  Olustur
                </Button>
              </HStack>
            </PopoverBody>
          </PopoverContent>
        </Popover>

        {/* Import template button */}
        <Popover
          placement="bottom-start"
          isOpen={showTemplates}
          onClose={() => setShowTemplates(false)}
        >
          <PopoverTrigger>
            <Tooltip label="Template Import Et" fontSize="xs">
              <Button
                size="xs"
                variant="ghost"
                fontSize="11px"
                fontWeight="500"
                flexShrink={0}
                onClick={handleShowTemplates}
              >
                Import
              </Button>
            </Tooltip>
          </PopoverTrigger>
          <PopoverContent w="320px">
            <PopoverBody maxH="240px" overflowY="auto">
              {templates.length === 0 ? (
                <Text fontSize="12px" color="gray.500" py={2} textAlign="center">
                  Paylasilan template bulunamadi
                </Text>
              ) : (
                templates.map((t) => (
                  <Flex
                    key={t.workflow_id}
                    justify="space-between"
                    align="center"
                    py={1.5}
                    px={1}
                    borderRadius="4px"
                    _hover={{ bg: 'gray.50' }}
                    cursor="pointer"
                    onClick={() => handleImportTemplate(t)}
                  >
                    <Box>
                      <Text fontSize="12px" fontWeight="500">{t.name}</Text>
                      <Text fontSize="10px" color="gray.500">
                        {t.agent_name} &middot; {t.node_count} node &middot; v{t.version}
                      </Text>
                    </Box>
                    <Badge fontSize="9px" colorScheme="blue" variant="subtle">
                      Import
                    </Badge>
                  </Flex>
                ))
              )}
            </PopoverBody>
          </PopoverContent>
        </Popover>
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
