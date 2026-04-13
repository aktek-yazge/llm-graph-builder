import {
  Box,
  Flex,
  Text,
  IconButton,
  Badge,
  useColorMode,
} from '@chakra-ui/react';
import { CheckIcon, CloseIcon } from '@chakra-ui/icons';
import { useAgentContext } from '../context/AgentContext';

export default function DiscoveryQueue() {
  const { discoveries, approveDiscovery, rejectDiscovery } = useAgentContext();
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  const pending = discoveries.filter((d) => d.status === 'pending');

  if (pending.length === 0) return null;

  return (
    <Box borderTop="1px" borderColor={isDark ? 'gray.700' : 'gray.200'}>
      <Box px={4} py={2} bg={isDark ? 'orange.900' : 'orange.50'}>
        <Text fontSize="sm" fontWeight="semibold" color={isDark ? 'orange.300' : 'orange.700'}>
          Onay Bekleyen Kesfler ({pending.length})
        </Text>
      </Box>
      <Box maxH="48" overflowY="auto">
        {pending.map((d) => (
          <Flex
            key={`${d.discovery_type}-${d.name}`}
            align="center"
            justify="space-between"
            px={4}
            py={2}
            borderBottom="1px"
            borderColor={isDark ? 'gray.800' : 'gray.100'}
            _last={{ borderBottom: 'none' }}
          >
            <Box minW={0} flex={1}>
              <Flex align="center" gap={2}>
                <Badge
                  fontSize="10px"
                  colorScheme={d.discovery_type === 'entity' ? 'blue' : 'purple'}
                  textTransform="uppercase"
                >
                  {d.discovery_type === 'entity' ? 'E' : 'R'}
                </Badge>
                <Text fontSize="sm" fontWeight="medium" isTruncated>
                  {d.name}
                </Text>
              </Flex>
              <Text fontSize="xs" color="gray.500">
                {d.sample_count} belgede goruldu
                {d.sample_properties?.length > 0 && ` | props: ${d.sample_properties.slice(0, 3).join(', ')}`}
              </Text>
            </Box>
            <Flex gap={1} flexShrink={0} ml={2}>
              <IconButton
                aria-label="Onayla"
                icon={<CheckIcon />}
                size="xs"
                variant="ghost"
                color="green.500"
                onClick={() => approveDiscovery(d.name, d.discovery_type)}
              />
              <IconButton
                aria-label="Reddet"
                icon={<CloseIcon />}
                size="xs"
                variant="ghost"
                color="red.500"
                onClick={() => rejectDiscovery(d.name, d.discovery_type)}
              />
            </Flex>
          </Flex>
        ))}
      </Box>
    </Box>
  );
}
