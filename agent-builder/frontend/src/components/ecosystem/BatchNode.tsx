import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box, Text, Badge, Flex, Progress } from '@chakra-ui/react';

export interface BatchNodeData {
  label: string;
  activeCount: number;
  latestStatus: string;
  total: number;
  processed: number;
  percentComplete: number;
  [key: string]: unknown;
}

function BatchNode({ data, selected }: NodeProps) {
  const d = data as unknown as BatchNodeData;
  const hasBatch = d.activeCount > 0;
  return (
    <Box
      bg="teal.500"
      color="white"
      borderRadius="lg"
      px={4}
      py={3}
      minW="180px"
      border="2px solid"
      borderColor={selected ? 'yellow.300' : 'teal.300'}
      boxShadow={selected ? '0 0 0 3px rgba(56,178,172,0.5)' : 'md'}
      opacity={hasBatch ? 1 : 0.55}
      cursor="pointer"
    >
      <Handle type="target" position={Position.Left} style={{ background: '#4fd1c5' }} />
      <Flex align="center" gap={2} mb={1}>
        <Text fontSize="md">📦</Text>
        <Text fontSize="sm" fontWeight="bold">{d.label}</Text>
      </Flex>
      {hasBatch ? (
        <>
          <Progress
            value={d.percentComplete}
            size="xs"
            colorScheme="green"
            borderRadius="full"
            mb={1}
          />
          <Flex gap={1} wrap="wrap">
            <Badge colorScheme="whiteAlpha" fontSize="2xs">
              {d.processed}/{d.total}
            </Badge>
            <Badge colorScheme="yellow" fontSize="2xs">{d.latestStatus}</Badge>
          </Flex>
        </>
      ) : (
        <Badge colorScheme="whiteAlpha" fontSize="2xs">no active batch</Badge>
      )}
      <Handle type="source" position={Position.Right} style={{ background: '#4fd1c5' }} />
    </Box>
  );
}

export default memo(BatchNode);
