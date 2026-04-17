import { useState, type ReactNode, type ElementType } from 'react';
import {
  Box,
  Flex,
  HStack,
  Icon,
  Text,
  Badge,
  Spinner,
  useColorMode,
  Collapse,
} from '@chakra-ui/react';
import { ChevronDownIcon, ChevronUpIcon } from '@chakra-ui/icons';

export type CompactStatus = 'running' | 'done' | 'error';

export interface CompactToolWrapperProps {
  toolName: string;
  icon: ElementType;
  colorScheme: string;
  status?: CompactStatus;
  summary?: string;
  badges?: ReactNode;
  children?: ReactNode;
  defaultOpen?: boolean;
}

export default function CompactToolWrapper({
  toolName,
  icon,
  colorScheme,
  status = 'done',
  summary,
  badges,
  children,
  defaultOpen = false,
}: CompactToolWrapperProps) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const [open, setOpen] = useState(defaultOpen);

  const baseColor = isDark ? `${colorScheme}.300` : `${colorScheme}.600`;
  const dotColor = isDark ? `${colorScheme}.400` : `${colorScheme}.500`;

  const statusBadge =
    status === 'running' ? (
      <HStack spacing={1}>
        <Spinner size="xs" color={dotColor} />
      </HStack>
    ) : status === 'error' ? (
      <Badge colorScheme="red" variant="subtle" fontSize="2xs">hata</Badge>
    ) : null;

  return (
    <Box
      my={1.5}
      borderRadius="lg"
      border="1px solid"
      borderColor={isDark ? 'gray.700' : 'gray.200'}
      bg={isDark ? 'gray.800' : 'white'}
      overflow="hidden"
      transition="border-color 0.15s"
      _hover={{ borderColor: isDark ? 'gray.600' : 'gray.300' }}
    >
      <Flex
        align="center"
        px={3}
        py={1.5}
        cursor="pointer"
        onClick={() => setOpen((v) => !v)}
        _hover={{ bg: isDark ? 'gray.750' : 'gray.50' }}
      >
        <HStack spacing={2} flex={1} minW={0}>
          <Icon as={icon} boxSize={3} color={baseColor} />
          <Text fontSize="xs" fontWeight="semibold" color={baseColor} flexShrink={0}>
            {toolName}
          </Text>
          {summary && (
            <Text
              fontSize="xs"
              color={isDark ? 'gray.400' : 'gray.600'}
              noOfLines={1}
              flex={1}
              minW={0}
            >
              {summary}
            </Text>
          )}
        </HStack>
        <HStack spacing={1.5} flexShrink={0}>
          {badges}
          {statusBadge}
          {children && (
            <Icon
              as={open ? ChevronUpIcon : ChevronDownIcon}
              boxSize={3.5}
              color={isDark ? 'gray.500' : 'gray.400'}
            />
          )}
        </HStack>
      </Flex>
      {children && (
        <Collapse in={open} animateOpacity>
          <Box borderTop="1px solid" borderColor={isDark ? 'gray.700' : 'gray.100'}>
            {children}
          </Box>
        </Collapse>
      )}
    </Box>
  );
}
