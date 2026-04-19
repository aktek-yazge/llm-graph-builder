import { useState, useEffect, useCallback, useMemo, type FC, type DragEvent } from 'react';
import { Box, Text, VStack } from '@chakra-ui/react';
import { getWorkflowNodeTypes, type WorkflowNodeType } from '../../services/evolvingApi';

const NodePalette: FC = () => {
  const [types, setTypes] = useState<WorkflowNodeType[]>([]);

  useEffect(() => {
    getWorkflowNodeTypes()
      .then((r) => setTypes(r.data.node_types || []))
      .catch(() => {});
  }, []);

  const categories = useMemo(() => {
    const map = new Map<string, WorkflowNodeType[]>();
    types.forEach((t) => {
      const list = map.get(t.category) || [];
      list.push(t);
      map.set(t.category, list);
    });
    return map;
  }, [types]);

  const onDragStart = useCallback((e: DragEvent<HTMLDivElement>, typeId: string) => {
    e.dataTransfer.setData('application/workflow-node-type', typeId);
    e.dataTransfer.effectAllowed = 'move';
  }, []);

  return (
    <Box
      position="absolute"
      top={2}
      left={2}
      zIndex={10}
      bg="white"
      border="1px solid"
      borderColor="#e9ecef"
      borderRadius="12px"
      p={3}
      w="180px"
      maxH="calc(100% - 60px)"
      overflowY="auto"
      shadow="0 4px 12px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.04)"
      sx={{ '&::-webkit-scrollbar': { display: 'none' } }}
    >
      <Text fontSize="11px" fontWeight="600" mb={3} color="#868e96" textTransform="uppercase" letterSpacing="0.04em">
        Node Tipleri
      </Text>
      {[...categories.entries()].map(([cat, items]) => (
        <Box key={cat} mb={3}>
          <Text fontSize="10px" fontWeight="600" textTransform="uppercase" color="#adb5bd" mb={1.5} letterSpacing="0.04em">
            {cat}
          </Text>
          <VStack spacing={1} align="stretch">
            {items.map((t) => (
              <Box
                key={t.type_id}
                draggable
                onDragStart={(e) => onDragStart(e, t.type_id)}
                px={2.5}
                py={2}
                borderRadius="8px"
                border="1px solid"
                borderColor="#f1f3f5"
                bg="white"
                cursor="grab"
                _hover={{
                  borderColor: '#dee2e6',
                  shadow: '0 1px 3px rgba(0,0,0,0.04)',
                }}
                transition="all 0.12s"
              >
                <Box display="flex" alignItems="center" gap={1.5}>
                  <Box
                    w="6px"
                    h="6px"
                    borderRadius="full"
                    bg={t.color}
                    flexShrink={0}
                  />
                  <Text fontSize="12px" fontWeight="500" color="#212529" noOfLines={1}>
                    {t.label}
                  </Text>
                </Box>
              </Box>
            ))}
          </VStack>
        </Box>
      ))}
    </Box>
  );
};

export default NodePalette;
