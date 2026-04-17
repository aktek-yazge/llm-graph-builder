import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Box, Flex, Text, IconButton, Spinner, useColorModeValue, useToast,
} from '@chakra-ui/react';
import { ArrowBackIcon, RepeatIcon } from '@chakra-ui/icons';

import AgentNode from '../components/ecosystem/AgentNode';
import OntologyNode from '../components/ecosystem/OntologyNode';
import ConnectionNode from '../components/ecosystem/ConnectionNode';
import SubagentNode from '../components/ecosystem/SubagentNode';
import BatchNode from '../components/ecosystem/BatchNode';
import ResourceNode from '../components/ecosystem/ResourceNode';
import NodeDetailPanel from '../components/ecosystem/NodeDetailPanel';
import { buildGraph } from '../components/ecosystem/graphBuilder';
import { getEcosystem, type EcosystemData } from '../services/evolvingApi';

const nodeTypes = {
  agentNode: AgentNode,
  ontologyNode: OntologyNode,
  connectionNode: ConnectionNode,
  subagentNode: SubagentNode,
  batchNode: BatchNode,
  resourceNode: ResourceNode,
};

export default function EcosystemPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const toast = useToast();

  const [eco, setEco] = useState<EcosystemData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const headerBg = useColorModeValue('white', 'gray.800');
  const canvasBg = useColorModeValue('#f7fafc', '#1a202c');

  const fetchData = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    try {
      const res = await getEcosystem(agentId);
      setEco(res.data);
      const { nodes: n, edges: e } = buildGraph(res.data);
      setNodes(n);
      setEdges(e);
    } catch (err) {
      toast({
        title: 'Ecosystem verisi alinamadi',
        status: 'error',
        duration: 3000,
        isClosable: true,
      });
    } finally {
      setLoading(false);
    }
  }, [agentId, toast, setNodes, setEdges]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    setSelectedNode(node);
  }, []);

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
  }, []);

  const memoizedNodeTypes = useMemo(() => nodeTypes, []);

  if (!agentId) {
    return (
      <Flex h="100vh" align="center" justify="center">
        <Text>Agent ID eksik</Text>
      </Flex>
    );
  }

  return (
    <Flex h="100vh" direction="column">
      {/* Header */}
      <Flex
        h="50px"
        bg={headerBg}
        borderBottom="1px solid"
        borderColor="gray.200"
        align="center"
        px={4}
        gap={3}
        flexShrink={0}
      >
        <IconButton
          aria-label="Back"
          icon={<ArrowBackIcon />}
          size="sm"
          variant="ghost"
          onClick={() => navigate('/evolving')}
        />
        <Text fontSize="md" fontWeight="bold">
          {eco?.agent.name || agentId}
        </Text>
        <Text fontSize="sm" color="gray.500">Ecosystem Graph</Text>
        <Box flex={1} />
        <IconButton
          aria-label="Refresh"
          icon={loading ? <Spinner size="xs" /> : <RepeatIcon />}
          size="sm"
          variant="ghost"
          onClick={fetchData}
          isDisabled={loading}
        />
      </Flex>

      {/* Canvas + Detail Panel */}
      <Flex flex={1} overflow="hidden">
        <Box flex={1} bg={canvasBg}>
          {loading && nodes.length === 0 ? (
            <Flex h="100%" align="center" justify="center">
              <Spinner size="xl" />
            </Flex>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={handleNodeClick}
              onPaneClick={handlePaneClick}
              nodeTypes={memoizedNodeTypes}
              fitView
              fitViewOptions={{ padding: 0.2 }}
              minZoom={0.3}
              maxZoom={2}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={20} size={1} />
              <Controls showInteractive={false} />
              <MiniMap
                nodeStrokeWidth={3}
                pannable
                zoomable
                style={{ height: 100, width: 150 }}
              />
            </ReactFlow>
          )}
        </Box>
        {selectedNode && (
          <NodeDetailPanel
            node={selectedNode}
            eco={eco}
            onClose={() => setSelectedNode(null)}
          />
        )}
      </Flex>
    </Flex>
  );
}
