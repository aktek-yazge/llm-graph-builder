import { memo, type FC } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box, Flex, Text, IconButton } from '@chakra-ui/react';
import { CloseIcon, EditIcon } from '@chakra-ui/icons';

interface WorkflowNodeData {
  label: string;
  type: string;
  color: string;
  icon: string;
  status?: 'idle' | 'running' | 'completed' | 'failed';
  params?: Record<string, unknown>;
  inputPorts?: Array<{ name: string }>;
  outputPorts?: Array<{ name: string }>;
  onDelete?: (nodeId: string) => void;
  onEdit?: (nodeId: string) => void;
}

const statusLabels: Record<string, string> = {
  idle: 'IDLE',
  running: 'Running',
  completed: 'Done',
  failed: 'Failed',
};

const statusDotColors: Record<string, string> = {
  idle: '#adb5bd',
  running: '#4c6ef5',
  completed: '#51cf66',
  failed: '#ff6b6b',
};

// Output port adına göre semantik renk kodlaması (graphify-adopted quality_gate
// üç-yol output: pass → yeşil, fail → kırmızı, ambiguous → turuncu).
// Backend `node_registry`'den gelen output_ports.name ile eşleşir.
const portColorByName: Record<string, string> = {
  pass: '#51cf66',       // green
  fail: '#ff6b6b',       // red
  ambiguous: '#ffa94d',  // orange — düşük güven / human review gereksinimi
};

const PORT_COLOR_DEFAULT = '#dee2e6';

const colorForPort = (portName: string, isInput: boolean): string => {
  if (isInput) return PORT_COLOR_DEFAULT;
  return portColorByName[portName] ?? PORT_COLOR_DEFAULT;
};

const WorkflowNode: FC<NodeProps> = ({ id, data: rawData, selected }) => {
  const data = rawData as unknown as WorkflowNodeData;
  const status = data.status || 'idle';
  const inputPorts = data.inputPorts || [];
  const outputPorts = data.outputPorts || [];

  return (
    <Box
      bg="white"
      border="1px solid"
      borderColor={selected ? '#4c6ef5' : '#e9ecef'}
      borderRadius="12px"
      minW="180px"
      maxW="240px"
      shadow={selected ? '0 4px 12px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.04)' : '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.02)'}
      cursor="pointer"
      transition="all 0.15s ease"
      position="relative"
      role="group"
      _hover={{
        shadow: '0 4px 12px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.04)',
        borderColor: '#dee2e6',
      }}
      onDoubleClick={() => data.onEdit?.(id)}
    >
      {/* Action buttons - visible on hover */}
      <Flex
        position="absolute"
        top="-10px"
        right="-6px"
        gap={0.5}
        opacity={0}
        _groupHover={{ opacity: 1 }}
        transition="opacity 0.15s"
        zIndex={10}
      >
        <IconButton
          aria-label="Duzenle"
          icon={<EditIcon />}
          size="xs"
          variant="solid"
          colorScheme="blue"
          borderRadius="full"
          minW="22px"
          h="22px"
          fontSize="10px"
          shadow="sm"
          onClick={(e) => { e.stopPropagation(); data.onEdit?.(id); }}
        />
        <IconButton
          aria-label="Sil"
          icon={<CloseIcon />}
          size="xs"
          variant="solid"
          colorScheme="red"
          borderRadius="full"
          minW="22px"
          h="22px"
          fontSize="8px"
          shadow="sm"
          onClick={(e) => { e.stopPropagation(); data.onDelete?.(id); }}
        />
      </Flex>

      <Flex px={3.5} pt={3} pb={1} align="center" justify="space-between">
        <Flex align="center" gap={2} minW={0} flex={1}>
          <Box
            w="8px"
            h="8px"
            borderRadius="full"
            bg={data.color || '#868e96'}
            flexShrink={0}
          />
          <Text
            fontSize="13px"
            fontWeight="600"
            color="#212529"
            noOfLines={1}
          >
            {data.label || data.type}
          </Text>
        </Flex>
        <Flex align="center" gap={1} flexShrink={0} ml={2}>
          <Box
            w="5px"
            h="5px"
            borderRadius="full"
            bg={statusDotColors[status]}
            sx={status === 'running' ? {
              animation: 'pulse 1.5s ease-in-out infinite',
              '@keyframes pulse': {
                '0%, 100%': { opacity: 1 },
                '50%': { opacity: 0.4 },
              },
            } : undefined}
          />
          <Text fontSize="10px" fontWeight="500" color="#adb5bd" textTransform="uppercase" letterSpacing="0.03em">
            {statusLabels[status]}
          </Text>
        </Flex>
      </Flex>

      <Box px={3.5} pb={3} pt={0.5}>
        <Text fontSize="11px" color="#868e96">
          {data.type}
        </Text>
      </Box>

      {inputPorts.map((port, i) => {
        const c = colorForPort(port.name, true);
        return (
          <Handle
            key={`in-${port.name}`}
            type="target"
            position={Position.Left}
            id={port.name}
            title={port.name}
            style={{
              top: `${30 + i * 20}%`,
              background: c,
              width: 7,
              height: 7,
              border: '2px solid white',
              boxShadow: `0 0 0 1px ${c}`,
            }}
          />
        );
      })}

      {outputPorts.map((port, i) => {
        const c = colorForPort(port.name, false);
        return (
          <Handle
            key={`out-${port.name}`}
            type="source"
            position={Position.Right}
            id={port.name}
            title={port.name}
            style={{
              top: `${30 + i * 20}%`,
              background: c,
              width: 7,
              height: 7,
              border: '2px solid white',
              boxShadow: `0 0 0 1px ${c}`,
            }}
          />
        );
      })}
    </Box>
  );
};

export default memo(WorkflowNode);
