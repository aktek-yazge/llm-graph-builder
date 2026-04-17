import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box, Text, Badge, Flex } from '@chakra-ui/react';

export interface AgentNodeData {
  label: string;
  purpose: string;
  domain: string;
  mode: string;
  model: string;
  [key: string]: unknown;
}

function AgentNode({ data, selected }: NodeProps) {
  const d = data as unknown as AgentNodeData;
  return (
    <Box
      bg="blue.600"
      color="white"
      borderRadius="xl"
      px={5}
      py={4}
      minW="220px"
      border="3px solid"
      borderColor={selected ? 'yellow.300' : 'blue.400'}
      boxShadow={selected ? '0 0 0 3px rgba(66,153,225,0.5)' : 'lg'}
      cursor="pointer"
    >
      <Handle type="target" position={Position.Left} style={{ background: '#63b3ed' }} />
      <Flex align="center" gap={2} mb={1}>
        <Text fontSize="lg" fontWeight="bold">🤖</Text>
        <Text fontSize="md" fontWeight="bold" noOfLines={1}>{d.label}</Text>
      </Flex>
      {d.purpose && <Text fontSize="xs" opacity={0.85} noOfLines={1}>{d.purpose}</Text>}
      <Flex gap={2} mt={2} wrap="wrap">
        <Badge colorScheme="yellow" fontSize="2xs">{d.mode}</Badge>
        <Badge colorScheme="purple" fontSize="2xs">{d.model}</Badge>
      </Flex>
      <Handle type="source" position={Position.Right} style={{ background: '#63b3ed' }} />
    </Box>
  );
}

export default memo(AgentNode);
