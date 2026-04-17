import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box, Text, Badge, Flex } from '@chakra-ui/react';

export interface OntologyNodeData {
  label: string;
  entityCount: number;
  relCount: number;
  ruleCount: number;
  constraintCount: number;
  pendingDiscoveries: number;
  domain: string;
  [key: string]: unknown;
}

function OntologyNode({ data, selected }: NodeProps) {
  const d = data as unknown as OntologyNodeData;
  return (
    <Box
      bg="green.500"
      color="white"
      borderRadius="lg"
      px={4}
      py={3}
      minW="180px"
      border="2px solid"
      borderColor={selected ? 'yellow.300' : 'green.300'}
      boxShadow={selected ? '0 0 0 3px rgba(72,187,120,0.5)' : 'md'}
      cursor="pointer"
    >
      <Handle type="target" position={Position.Left} style={{ background: '#68d391' }} />
      <Flex align="center" gap={2} mb={1}>
        <Text fontSize="md">🧬</Text>
        <Text fontSize="sm" fontWeight="bold">{d.label}</Text>
      </Flex>
      <Flex gap={1} wrap="wrap">
        <Badge colorScheme="whiteAlpha" fontSize="2xs">{d.entityCount} entity</Badge>
        <Badge colorScheme="whiteAlpha" fontSize="2xs">{d.relCount} rel</Badge>
        {d.pendingDiscoveries > 0 && (
          <Badge colorScheme="orange" fontSize="2xs">{d.pendingDiscoveries} pending</Badge>
        )}
      </Flex>
      <Handle type="source" position={Position.Right} style={{ background: '#68d391' }} />
    </Box>
  );
}

export default memo(OntologyNode);
