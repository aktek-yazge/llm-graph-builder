import {
  Box, Text, Flex, Badge, Divider, VStack, IconButton,
  useColorModeValue,
} from '@chakra-ui/react';
import { CloseIcon } from '@chakra-ui/icons';
import type { Node } from '@xyflow/react';
import type { EcosystemData } from '../../services/evolvingApi';

interface Props {
  node: Node | null;
  eco: EcosystemData | null;
  onClose: () => void;
}

export default function NodeDetailPanel({ node, eco, onClose }: Props) {
  const bg = useColorModeValue('white', 'gray.800');
  const borderClr = useColorModeValue('gray.200', 'gray.600');

  if (!node || !eco) return null;

  return (
    <Box
      w="340px"
      h="100%"
      bg={bg}
      borderLeft="1px solid"
      borderColor={borderClr}
      overflowY="auto"
      p={4}
    >
      <Flex justify="space-between" align="center" mb={3}>
        <Text fontSize="md" fontWeight="bold">Node Details</Text>
        <IconButton
          aria-label="Close"
          icon={<CloseIcon />}
          size="xs"
          variant="ghost"
          onClick={onClose}
        />
      </Flex>
      <Divider mb={3} />
      <DetailContent nodeId={node.id} nodeType={node.type || ''} eco={eco} />
    </Box>
  );
}

function DetailContent({ nodeId, nodeType, eco }: { nodeId: string; nodeType: string; eco: EcosystemData }) {
  switch (nodeType) {
    case 'agentNode':
      return <AgentDetail eco={eco} />;
    case 'ontologyNode':
      return <OntologyDetail eco={eco} />;
    case 'connectionNode':
      return <ConnectionDetail nodeId={nodeId} eco={eco} />;
    case 'subagentNode':
      return <SubagentDetail nodeId={nodeId} eco={eco} />;
    case 'batchNode':
      return <BatchDetail eco={eco} />;
    case 'resourceNode':
      return <ResourceDetail eco={eco} />;
    default:
      return <Text fontSize="sm" color="gray.500">No details available</Text>;
  }
}

function Row({ label, value }: { label: string; value: string | number | boolean }) {
  return (
    <Flex justify="space-between" fontSize="sm" py={1}>
      <Text color="gray.500">{label}</Text>
      <Text fontWeight="medium">{String(value)}</Text>
    </Flex>
  );
}

function AgentDetail({ eco }: { eco: EcosystemData }) {
  const a = eco.agent;
  return (
    <VStack align="stretch" spacing={1}>
      <Row label="Agent ID" value={a.agent_id} />
      <Row label="Name" value={a.name} />
      <Row label="Purpose" value={a.purpose || '—'} />
      <Row label="Domain" value={a.domain || '—'} />
      <Row label="Mode" value={a.mode} />
      <Row label="Model" value={a.model} />
      <Divider my={2} />
      <Row label="Notifications" value={eco.notifications.recent_count} />
    </VStack>
  );
}

function OntologyDetail({ eco }: { eco: EcosystemData }) {
  const o = eco.ontology;
  const d = eco.discoveries;
  return (
    <VStack align="stretch" spacing={1}>
      <Row label="Domain" value={o.domain || '—'} />
      <Row label="Goal" value={o.goal || '—'} />
      <Divider my={2} />
      <Row label="Entity Classes" value={o.entity_count} />
      <Row label="Relationships" value={o.relationship_count} />
      <Row label="Inference Rules" value={o.rule_count} />
      <Row label="Constraints" value={o.constraint_count} />
      <Divider my={2} />
      <Text fontSize="xs" fontWeight="bold" color="gray.500" textTransform="uppercase">Discoveries</Text>
      <Flex gap={2} wrap="wrap">
        <Badge colorScheme="orange">{d.pending} pending</Badge>
        <Badge colorScheme="green">{d.approved} approved</Badge>
        <Badge colorScheme="red">{d.rejected} rejected</Badge>
      </Flex>
    </VStack>
  );
}

function ConnectionDetail({ nodeId, eco }: { nodeId: string; eco: EcosystemData }) {
  if (nodeId === 'mcp') {
    const m = eco.mcp;
    return (
      <VStack align="stretch" spacing={1}>
        <Row label="Connected" value={m.connected} />
        <Row label="Tools" value={m.tool_count} />
        {m.tool_names.length > 0 && (
          <>
            <Divider my={2} />
            <Text fontSize="xs" fontWeight="bold" color="gray.500">Tool List</Text>
            {m.tool_names.map((t) => (
              <Badge key={t} fontSize="2xs" colorScheme="purple" mr={1} mb={1}>{t}</Badge>
            ))}
          </>
        )}
      </VStack>
    );
  }
  if (nodeId === 'celery') {
    return (
      <VStack align="stretch" spacing={1}>
        <Row label="Available" value={eco.celery.available} />
        <Text fontSize="xs" color="gray.500" mt={2}>
          Pipeline: extract_images → ocr_pages → extract_entities
        </Text>
      </VStack>
    );
  }
  if (nodeId === 'neo4j') {
    return (
      <VStack align="stretch" spacing={1}>
        <Row label="Configured" value={eco.neo4j.configured} />
        <Row label="URI" value={eco.neo4j.uri || '—'} />
      </VStack>
    );
  }
  return null;
}

function SubagentDetail({ nodeId, eco }: { nodeId: string; eco: EcosystemData }) {
  const idx = parseInt(nodeId.replace('subagent-', ''), 10);
  const sa = eco.subagents[idx];
  if (!sa) return <Text fontSize="sm">Unknown subagent</Text>;
  return (
    <VStack align="stretch" spacing={1}>
      <Row label="Name" value={sa.name} />
      <Row label="Tools" value={sa.tool_count} />
      <Divider my={2} />
      <Text fontSize="sm">{sa.description}</Text>
    </VStack>
  );
}

function BatchDetail({ eco }: { eco: EcosystemData }) {
  const b = eco.batch;
  return (
    <VStack align="stretch" spacing={1}>
      <Row label="Active Batches" value={b.active_count} />
      {b.latest ? (
        <>
          <Divider my={2} />
          <Text fontSize="xs" fontWeight="bold" color="gray.500">Latest Batch</Text>
          <Row label="Batch ID" value={b.latest.batch_id} />
          <Row label="Status" value={b.latest.status} />
          <Row label="Total" value={b.latest.total} />
          <Row label="Processed" value={b.latest.processed} />
          <Row label="Progress" value={`${b.latest.percent_complete}%`} />
        </>
      ) : (
        <Text fontSize="sm" color="gray.500" mt={2}>No active batch</Text>
      )}
    </VStack>
  );
}

function ResourceDetail({ eco }: { eco: EcosystemData }) {
  const r = eco.resources;
  return (
    <VStack align="stretch" spacing={1}>
      <Row label="Sample Files" value={r.sample_files} />
      <Row label="Source URLs" value={r.source_urls} />
      <Row label="Total" value={r.sample_files + r.source_urls} />
    </VStack>
  );
}
