import { useEffect, useCallback, useState } from 'react';
import {
  Box,
  Flex,
  Text,
  IconButton,
  Badge,
  Progress,
  Collapse,
  SimpleGrid,
  useColorMode,
} from '@chakra-ui/react';
import { RepeatIcon, ChevronUpIcon, ChevronDownIcon } from '@chakra-ui/icons';
import { useAgentContext } from '../context/AgentContext';

export default function BatchMonitor() {
  const { activeAgent, batchProgress, refreshBatch } = useAgentContext();
  const [expanded, setExpanded] = useState(false);
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  const poll = useCallback(() => {
    if (!activeAgent) return;
    refreshBatch();
  }, [activeAgent, refreshBatch]);

  useEffect(() => {
    if (!activeAgent) return;
    poll();
    const interval = setInterval(poll, 15000);
    return () => clearInterval(interval);
  }, [activeAgent, poll]);

  if (!batchProgress || !activeAgent) return null;

  const { status, total, processed, successful, failed, needs_review, percent_complete } = batchProgress;

  if (total === 0) return null;

  const barScheme =
    status === 'completed' ? 'green' : status === 'failed' ? 'red' : 'blue';

  return (
    <Box
      borderTop="1px"
      borderColor={isDark ? 'gray.700' : 'gray.200'}
      bg={isDark ? 'gray.800' : 'gray.50'}
    >
      <Flex
        align="center"
        justify="space-between"
        px={4}
        py={2}
        cursor="pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <Flex align="center" gap={2}>
          <Text fontSize="sm" fontWeight="semibold">
            Batch Isleme
          </Text>
          <Badge
            fontSize="10px"
            colorScheme={barScheme}
            textTransform="uppercase"
          >
            {status}
          </Badge>
        </Flex>
        <Flex align="center" gap={2}>
          <Text fontSize="sm" color="gray.500">
            {processed}/{total}
          </Text>
          <IconButton
            aria-label="Yenile"
            icon={<RepeatIcon />}
            size="xs"
            variant="ghost"
            onClick={(e) => {
              e.stopPropagation();
              poll();
            }}
          />
          {expanded ? <ChevronDownIcon color="gray.400" /> : <ChevronUpIcon color="gray.400" />}
        </Flex>
      </Flex>

      <Box px={4} pb={2}>
        <Progress
          value={Math.min(percent_complete, 100)}
          size="xs"
          colorScheme={barScheme}
          borderRadius="full"
        />
      </Box>

      <Collapse in={expanded}>
        <SimpleGrid columns={2} gap={2} px={4} pb={3} fontSize="xs">
          <Flex justify="space-between">
            <Text color="gray.500">Basarili</Text>
            <Text color="green.500" fontWeight="medium">{successful}</Text>
          </Flex>
          <Flex justify="space-between">
            <Text color="gray.500">Basarisiz</Text>
            <Text color="red.500" fontWeight="medium">{failed}</Text>
          </Flex>
          <Flex justify="space-between">
            <Text color="gray.500">Inceleme Bekliyor</Text>
            <Text color="orange.500" fontWeight="medium">{needs_review}</Text>
          </Flex>
          <Flex justify="space-between">
            <Text color="gray.500">Ilerleme</Text>
            <Text fontWeight="medium">{percent_complete.toFixed(1)}%</Text>
          </Flex>
        </SimpleGrid>
      </Collapse>
    </Box>
  );
}
