import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box, Text, Badge, Flex } from '@chakra-ui/react';

export interface ConnectionNodeData {
  label: string;
  connectionType: 'mcp' | 'celery' | 'neo4j';
  connected: boolean;
  detail: string;
  [key: string]: unknown;
}

const ICONS: Record<string, string> = {
  mcp: '🔌',
  celery: '⚙️',
  neo4j: '🗄️',
};

const COLORS: Record<string, string> = {
  mcp: 'purple.500',
  celery: 'orange.500',
  neo4j: 'cyan.600',
};

function ConnectionNode({ data, selected }: NodeProps) {
  const d = data as unknown as ConnectionNodeData;
  const bg = COLORS[d.connectionType] || 'gray.500';
  return (
    <Box
      bg={bg}
      color="white"
      borderRadius="lg"
      px={4}
      py={3}
      minW="160px"
      border="2px solid"
      borderColor={selected ? 'yellow.300' : 'transparent'}
      boxShadow={selected ? '0 0 0 3px rgba(159,122,234,0.5)' : 'md'}
      opacity={d.connected ? 1 : 0.55}
      cursor="pointer"
    >
      <Handle type="target" position={Position.Left} style={{ background: '#b794f4' }} />
      <Flex align="center" gap={2} mb={1}>
        <Text fontSize="md">{ICONS[d.connectionType] || '🔗'}</Text>
        <Text fontSize="sm" fontWeight="bold">{d.label}</Text>
      </Flex>
      <Flex gap={1}>
        <Badge colorScheme={d.connected ? 'green' : 'red'} fontSize="2xs">
          {d.connected ? 'connected' : 'offline'}
        </Badge>
        {d.detail && <Badge colorScheme="whiteAlpha" fontSize="2xs">{d.detail}</Badge>}
      </Flex>
      <Handle type="source" position={Position.Right} style={{ background: '#b794f4' }} />
    </Box>
  );
}

export default memo(ConnectionNode);
