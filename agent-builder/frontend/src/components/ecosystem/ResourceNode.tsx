import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box, Text, Badge, Flex } from '@chakra-ui/react';

export interface ResourceNodeData {
  label: string;
  sampleFiles: number;
  sourceUrls: number;
  [key: string]: unknown;
}

function ResourceNode({ data, selected }: NodeProps) {
  const d = data as unknown as ResourceNodeData;
  const total = d.sampleFiles + d.sourceUrls;
  return (
    <Box
      bg="yellow.500"
      color="gray.800"
      borderRadius="lg"
      px={4}
      py={3}
      minW="160px"
      border="2px solid"
      borderColor={selected ? 'blue.400' : 'yellow.300'}
      boxShadow={selected ? '0 0 0 3px rgba(236,201,75,0.5)' : 'md'}
      opacity={total > 0 ? 1 : 0.55}
      cursor="pointer"
    >
      <Handle type="source" position={Position.Right} style={{ background: '#ecc94b' }} />
      <Flex align="center" gap={2} mb={1}>
        <Text fontSize="md">📁</Text>
        <Text fontSize="sm" fontWeight="bold">{d.label}</Text>
      </Flex>
      <Flex gap={1} wrap="wrap">
        <Badge colorScheme="blackAlpha" fontSize="2xs">{d.sampleFiles} files</Badge>
        <Badge colorScheme="blackAlpha" fontSize="2xs">{d.sourceUrls} urls</Badge>
      </Flex>
    </Box>
  );
}

export default memo(ResourceNode);
