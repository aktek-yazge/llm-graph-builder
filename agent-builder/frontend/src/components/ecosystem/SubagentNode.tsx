import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box, Text, Badge, Flex } from '@chakra-ui/react';

export interface SubagentNodeData {
  label: string;
  description: string;
  toolCount: number;
  [key: string]: unknown;
}

function SubagentNode({ data, selected }: NodeProps) {
  const d = data as unknown as SubagentNodeData;
  return (
    <Box
      bg="pink.500"
      color="white"
      borderRadius="lg"
      px={4}
      py={3}
      minW="170px"
      border="2px solid"
      borderColor={selected ? 'yellow.300' : 'pink.300'}
      boxShadow={selected ? '0 0 0 3px rgba(237,100,166,0.5)' : 'md'}
      cursor="pointer"
    >
      <Handle type="target" position={Position.Left} style={{ background: '#f687b3' }} />
      <Flex align="center" gap={2} mb={1}>
        <Text fontSize="md">🧠</Text>
        <Text fontSize="sm" fontWeight="bold">{d.label}</Text>
      </Flex>
      <Text fontSize="2xs" opacity={0.85} noOfLines={2}>{d.description}</Text>
      <Badge colorScheme="whiteAlpha" fontSize="2xs" mt={1}>{d.toolCount} tools</Badge>
      <Handle type="source" position={Position.Right} style={{ background: '#f687b3' }} />
    </Box>
  );
}

export default memo(SubagentNode);
