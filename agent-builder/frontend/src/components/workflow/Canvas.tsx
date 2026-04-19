import {
  useState,
  useEffect,
  useCallback,
  useRef,
  type FC,
  type DragEvent,
} from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  type Connection,
  type Node,
  type Edge,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Box,
  useColorMode,
  useToast,
  Drawer,
  DrawerOverlay,
  DrawerContent,
  DrawerCloseButton,
  DrawerHeader,
  DrawerBody,
  DrawerFooter,
  FormControl,
  FormLabel,
  Input,
  Textarea,
  Button,
  VStack,
  Text,
  Badge,
  useDisclosure,
} from '@chakra-ui/react';

import WorkflowNode from './nodes/WorkflowNode';
import NodePalette from './NodePalette';
import Toolbar from './Toolbar';
import { useAgentContext } from '../../context/AgentContext';
import {
  getWorkflowById,
  getWorkflow,
  getWorkflowNodeTypes,
  updateWorkflowById,
  updateWorkflow,
  startWorkflowRunById,
  startWorkflowRun,
  publishWorkflowById,
  publishWorkflow as publishWorkflowApi,
  type WorkflowData,
  type WorkflowNodeType,
  type WorkflowDSL,
} from '../../services/evolvingApi';

const nodeTypes = { workflowNode: WorkflowNode };

function dslToReactFlow(
  dsl: WorkflowDSL,
  nodeTypeCatalog: Map<string, WorkflowNodeType>,
  callbacks: { onDelete: (id: string) => void; onEdit: (id: string) => void },
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = dsl.nodes.map((n) => {
    const catalog = nodeTypeCatalog.get(n.type);
    return {
      id: n.id,
      type: 'workflowNode',
      position: { x: n.position.x, y: n.position.y },
      data: {
        label: n.label || catalog?.label || n.type,
        type: n.type,
        color: catalog?.color || '#718096',
        icon: catalog?.icon || 'settings',
        params: n.params,
        inputPorts: catalog?.input_ports?.map((p) => ({ name: p.name })) || [],
        outputPorts: catalog?.output_ports?.map((p) => ({ name: p.name })) || [],
        status: 'idle',
        onDelete: callbacks.onDelete,
        onEdit: callbacks.onEdit,
      },
    };
  });

  const edges: Edge[] = dsl.edges.map((e, i) => ({
    id: `edge-${i}`,
    source: e.from_node,
    sourceHandle: e.from_port,
    target: e.to_node,
    targetHandle: e.to_port,
    label: e.condition || undefined,
    type: 'smoothstep',
    animated: false,
    style: { stroke: '#ced4da', strokeWidth: 1.5 },
  }));

  return { nodes, edges };
}

function reactFlowToDsl(nodes: Node[], edges: Edge[]): WorkflowDSL {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: (n.data as Record<string, unknown>)?.type as string || 'unknown',
      label: (n.data as Record<string, unknown>)?.label as string || '',
      params: ((n.data as Record<string, unknown>)?.params as Record<string, unknown>) || {},
      position: { x: n.position.x, y: n.position.y },
    })),
    edges: edges.map((e) => ({
      from_node: e.source,
      from_port: e.sourceHandle || 'out',
      to_node: e.target,
      to_port: e.targetHandle || 'in',
      condition: (e.label as string) || undefined,
    })),
  };
}

const WorkflowCanvas: FC<{ workflowId?: string | null }> = ({ workflowId: propWorkflowId }) => {
  const { activeAgent, notifications } = useAgentContext();
  const { colorMode } = useColorMode();
  const toast = useToast();
  const isDark = colorMode === 'dark';
  const agentId = activeAgent?.agent_id ?? '';
  const workflowId = propWorkflowId ?? '';

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([] as Node[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([] as Edge[]);
  const [workflowData, setWorkflowData] = useState<WorkflowData | null>(null);
  const [nodeTypeCatalog, setNodeTypeCatalog] = useState<Map<string, WorkflowNodeType>>(new Map());
  const [isRunning, setIsRunning] = useState(false);
  const rfInstanceRef = useRef<ReactFlowInstance<Node, Edge> | null>(null);
  const saveTimeout = useRef<ReturnType<typeof setTimeout>>();

  const { isOpen: isEditOpen, onOpen: openEdit, onClose: closeEdit } = useDisclosure();
  const [editingNodeId, setEditingNodeId] = useState<string | null>(null);
  const [editLabel, setEditLabel] = useState('');
  const [editParams, setEditParams] = useState('');

  const persistDsl = useCallback(
    (newNodes: Node[], newEdges: Edge[]) => {
      if (!agentId) return;
      if (saveTimeout.current) clearTimeout(saveTimeout.current);
      saveTimeout.current = setTimeout(() => {
        const dsl = reactFlowToDsl(newNodes, newEdges);
        if (workflowId) {
          updateWorkflowById(agentId, workflowId, dsl).catch(() => {});
        } else {
          updateWorkflow(agentId, dsl).catch(() => {});
        }
      }, 800);
    },
    [agentId, workflowId],
  );

  const persistNow = useCallback(
    (newNodes?: Node[], newEdges?: Edge[]) => {
      if (!agentId) return;
      if (saveTimeout.current) clearTimeout(saveTimeout.current);
      const n = newNodes ?? rfInstanceRef.current?.getNodes() ?? [];
      const e = newEdges ?? rfInstanceRef.current?.getEdges() ?? [];
      const dsl = reactFlowToDsl(n, e);
      if (workflowId) {
        updateWorkflowById(agentId, workflowId, dsl).catch(() => {});
      } else {
        updateWorkflow(agentId, dsl).catch(() => {});
      }
    },
    [agentId, workflowId],
  );

  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes((prev) => {
        const next = prev.filter((n) => n.id !== nodeId);
        setEdges((prevEdges) => {
          const nextEdges = prevEdges.filter((e) => e.source !== nodeId && e.target !== nodeId);
          persistDsl(next, nextEdges);
          return nextEdges;
        });
        return next;
      });
      toast({ title: 'Node silindi', status: 'info', duration: 1500 });
    },
    [persistDsl, toast],
  );

  const handleEditNode = useCallback(
    (nodeId: string) => {
      const node = rfInstanceRef.current?.getNode(nodeId);
      if (!node) return;
      const d = node.data as Record<string, unknown>;
      setEditingNodeId(nodeId);
      setEditLabel((d.label as string) || '');
      try {
        setEditParams(JSON.stringify(d.params || {}, null, 2));
      } catch {
        setEditParams('{}');
      }
      openEdit();
    },
    [openEdit],
  );

  const handleSaveEdit = useCallback(() => {
    if (!editingNodeId) return;
    let parsedParams: Record<string, unknown> = {};
    try {
      parsedParams = JSON.parse(editParams);
    } catch {
      toast({ title: 'Gecersiz JSON', status: 'error', duration: 2000 });
      return;
    }

    setNodes((prev) => {
      const next = prev.map((n) => {
        if (n.id !== editingNodeId) return n;
        const d = n.data as Record<string, unknown>;
        return {
          ...n,
          data: { ...d, label: editLabel, params: parsedParams },
        };
      });
      persistNow(next, rfInstanceRef.current?.getEdges() ?? []);
      return next;
    });

    toast({ title: 'Node guncellendi', status: 'success', duration: 1500 });
    closeEdit();
    setEditingNodeId(null);
  }, [editingNodeId, editLabel, editParams, persistNow, toast, closeEdit]);

  const nodeCallbacks = useRef({ onDelete: handleDeleteNode, onEdit: handleEditNode });
  useEffect(() => {
    nodeCallbacks.current = { onDelete: handleDeleteNode, onEdit: handleEditNode };
  }, [handleDeleteNode, handleEditNode]);

  useEffect(() => {
    getWorkflowNodeTypes()
      .then((r) => {
        const map = new Map<string, WorkflowNodeType>();
        (r.data.node_types || []).forEach((t) => map.set(t.type_id, t));
        setNodeTypeCatalog(map);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!agentId) return;
    const loadFn = workflowId
      ? () => getWorkflowById(agentId, workflowId)
      : () => getWorkflow(agentId);
    loadFn()
      .then((r) => {
        setWorkflowData(r.data);
        const cbs = { onDelete: (id: string) => nodeCallbacks.current.onDelete(id), onEdit: (id: string) => nodeCallbacks.current.onEdit(id) };
        const { nodes: n, edges: e } = dslToReactFlow(r.data.dsl, nodeTypeCatalog, cbs);
        setNodes(n);
        setEdges(e);
      })
      .catch(() => {});
  }, [agentId, workflowId, nodeTypeCatalog]);

  useEffect(() => {
    if (!notifications.length || !agentId) return;
    const latest = notifications[0];
    if (latest?.event_type === 'workflow_changed') {
      const changedId = (latest.data as Record<string, unknown>)?.workflow_id as string | undefined;
      if (workflowId && changedId && changedId !== workflowId) return;
      const loadFn = workflowId
        ? () => getWorkflowById(agentId, workflowId)
        : () => getWorkflow(agentId);
      loadFn()
        .then((r) => {
          setWorkflowData(r.data);
          const cbs = { onDelete: (id: string) => nodeCallbacks.current.onDelete(id), onEdit: (id: string) => nodeCallbacks.current.onEdit(id) };
          const { nodes: n, edges: e } = dslToReactFlow(r.data.dsl, nodeTypeCatalog, cbs);
          setNodes(n);
          setEdges(e);
        })
        .catch(() => {});
    }
    if (latest?.event_type === 'run_started') setIsRunning(true);
    if (latest?.event_type === 'run_complete') setIsRunning(false);
  }, [notifications, agentId, workflowId, nodeTypeCatalog]);

  const onConnect = useCallback(
    (conn: Connection) => {
      setEdges((eds) => {
        const next = addEdge(conn, eds);
        persistDsl(nodes, next);
        return next;
      });
    },
    [nodes, persistDsl],
  );

  const handleNodesChange: typeof onNodesChange = useCallback(
    (changes) => {
      onNodesChange(changes);
      const hasPositionChange = changes.some((c) => c.type === 'position' && 'position' in c && c.position);
      const hasRemoveChange = changes.some((c) => c.type === 'remove');
      if (hasPositionChange || hasRemoveChange) {
        setTimeout(() => {
          persistDsl(
            rfInstanceRef.current?.getNodes() || [],
            rfInstanceRef.current?.getEdges() || [],
          );
        }, 50);
      }
    },
    [onNodesChange, persistDsl],
  );

  const handleEdgesChange: typeof onEdgesChange = useCallback(
    (changes) => {
      onEdgesChange(changes);
      const hasRemoveChange = changes.some((c) => c.type === 'remove');
      if (hasRemoveChange) {
        setTimeout(() => {
          persistDsl(
            rfInstanceRef.current?.getNodes() || [],
            rfInstanceRef.current?.getEdges() || [],
          );
        }, 50);
      }
    },
    [onEdgesChange, persistDsl],
  );

  const onDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      const typeId = e.dataTransfer.getData('application/workflow-node-type');
      if (!typeId || !rfInstanceRef.current) return;
      const catalog = nodeTypeCatalog.get(typeId);
      if (!catalog) return;

      const bounds = (e.target as HTMLElement).closest('.react-flow')?.getBoundingClientRect();
      if (!bounds) return;
      const position = rfInstanceRef.current.screenToFlowPosition({
        x: e.clientX - bounds.left,
        y: e.clientY - bounds.top,
      });

      const newId = `${typeId}-${Math.random().toString(36).slice(2, 8)}`;
      const newNode: Node = {
        id: newId,
        type: 'workflowNode',
        position,
        data: {
          label: catalog.label,
          type: typeId,
          color: catalog.color,
          icon: catalog.icon,
          params: {},
          inputPorts: catalog.input_ports?.map((p) => ({ name: p.name })) || [],
          outputPorts: catalog.output_ports?.map((p) => ({ name: p.name })) || [],
          status: 'idle',
          onDelete: (id: string) => nodeCallbacks.current.onDelete(id),
          onEdit: (id: string) => nodeCallbacks.current.onEdit(id),
        },
      };

      setNodes((prev) => {
        const next = [...prev, newNode];
        persistDsl(next, edges);
        return next;
      });
    },
    [nodeTypeCatalog, edges, persistDsl],
  );

  const onDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const handleValidate = useCallback(() => {
    toast({ title: 'Dogrulama baslatildi...', status: 'info', duration: 2000, isClosable: true });
  }, [toast]);

  const handleTestRun = useCallback(() => {
    if (!agentId) return;
    setIsRunning(true);
    const runFn = workflowId
      ? () => startWorkflowRunById(agentId, workflowId, 'test_one')
      : () => startWorkflowRun(agentId, 'test_one');
    runFn()
      .then((r) => {
        toast({
          title: `Test: ${r.data.status}`,
          description: r.data.errors?.join(', ') || 'OK',
          status: r.data.status === 'completed' ? 'success' : 'warning',
          duration: 4000,
          isClosable: true,
        });
      })
      .catch((err) => {
        toast({ title: 'Test hatasi', description: String(err), status: 'error', duration: 4000 });
      })
      .finally(() => setIsRunning(false));
  }, [agentId, workflowId, toast]);

  const handleFullRun = useCallback(() => {
    if (!agentId) return;
    setIsRunning(true);
    const runFn = workflowId
      ? () => startWorkflowRunById(agentId, workflowId, 'full_batch')
      : () => startWorkflowRun(agentId, 'full_batch');
    runFn()
      .then((r) => {
        toast({
          title: `Batch: ${r.data.status}`,
          status: r.data.status === 'completed' ? 'success' : 'info',
          duration: 4000,
          isClosable: true,
        });
      })
      .catch((err) => {
        toast({ title: 'Batch hatasi', description: String(err), status: 'error', duration: 4000 });
      })
      .finally(() => setIsRunning(false));
  }, [agentId, workflowId, toast]);

  const handlePublish = useCallback(() => {
    if (!agentId) return;
    const pubFn = workflowId
      ? () => publishWorkflowById(agentId, workflowId)
      : () => publishWorkflowApi(agentId);
    pubFn()
      .then((r) => {
        toast({
          title: `Yayinlandi: v${r.data.version}`,
          status: 'success',
          duration: 4000,
          isClosable: true,
        });
      })
      .catch((err) => {
        toast({ title: 'Yayin hatasi', description: String(err), status: 'error', duration: 4000 });
      });
  }, [agentId, workflowId, toast]);

  const editingNode = editingNodeId ? nodes.find((n) => n.id === editingNodeId) : null;
  const editingType = editingNode ? (editingNode.data as Record<string, unknown>)?.type as string : '';

  return (
    <Box position="relative" w="100%" h="100%">
      <NodePalette />
      <Toolbar
        workflowName={workflowData?.name || 'Workflow'}
        workflowStatus={workflowData?.status || 'draft'}
        version={workflowData?.version || 1}
        nodeCount={nodes.length}
        onValidate={handleValidate}
        onTestRun={handleTestRun}
        onFullRun={handleFullRun}
        onPublish={handlePublish}
        isRunning={isRunning}
      />
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        onInit={(instance) => { rfInstanceRef.current = instance; }}
        onDrop={onDrop}
        onDragOver={onDragOver}
        nodeTypes={nodeTypes}
        fitView
        deleteKeyCode={['Delete', 'Backspace']}
        colorMode={isDark ? 'dark' : 'light'}
      >
        <Background variant={'dots' as any} gap={20} size={1} color={isDark ? '#374151' : '#e9ecef'} />
        <Controls
          showInteractive={false}
          style={{
            border: '1px solid #e9ecef',
            borderRadius: '8px',
            boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
            overflow: 'hidden',
          }}
        />
        <MiniMap
          nodeColor={() => '#dee2e6'}
          maskColor="rgba(255,255,255,0.85)"
          style={{ height: 80, width: 120, border: '1px solid #e9ecef', borderRadius: '8px' }}
        />
      </ReactFlow>

      {/* Node Edit Drawer */}
      <Drawer isOpen={isEditOpen} placement="right" onClose={closeEdit} size="md">
        <DrawerOverlay />
        <DrawerContent>
          <DrawerCloseButton />
          <DrawerHeader borderBottomWidth="1px" fontSize="md">
            Node Ayarlari
            {editingType && (
              <Badge ml={2} colorScheme="blue" fontSize="xs">{editingType}</Badge>
            )}
          </DrawerHeader>
          <DrawerBody>
            <VStack spacing={4} align="stretch" pt={2}>
              <FormControl>
                <FormLabel fontSize="sm">Etiket (Label)</FormLabel>
                <Input
                  size="sm"
                  value={editLabel}
                  onChange={(e) => setEditLabel(e.target.value)}
                  placeholder="Node etiketi"
                />
              </FormControl>

              <FormControl>
                <FormLabel fontSize="sm">
                  Parametreler (JSON)
                </FormLabel>
                <Textarea
                  size="sm"
                  value={editParams}
                  onChange={(e) => setEditParams(e.target.value)}
                  fontFamily="mono"
                  fontSize="xs"
                  rows={10}
                  resize="vertical"
                  placeholder='{"key": "value"}'
                />
                <Text fontSize="xs" color="gray.500" mt={1}>
                  Node'a ozel konfigürasyon. JSON formatında.
                </Text>
              </FormControl>

              {editingNodeId && (
                <Box>
                  <Text fontSize="xs" color="gray.400">Node ID: {editingNodeId}</Text>
                </Box>
              )}
            </VStack>
          </DrawerBody>
          <DrawerFooter borderTopWidth="1px" gap={2}>
            <Button size="sm" variant="ghost" onClick={closeEdit}>Iptal</Button>
            <Button size="sm" colorScheme="blue" onClick={handleSaveEdit}>Kaydet</Button>
            <Button
              size="sm"
              colorScheme="red"
              variant="outline"
              onClick={() => {
                if (editingNodeId) {
                  handleDeleteNode(editingNodeId);
                  closeEdit();
                  setEditingNodeId(null);
                }
              }}
            >
              Sil
            </Button>
          </DrawerFooter>
        </DrawerContent>
      </Drawer>
    </Box>
  );
};

export default WorkflowCanvas;
