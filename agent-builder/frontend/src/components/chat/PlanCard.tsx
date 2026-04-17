import { Box, Flex, Text, Badge, useColorMode, HStack } from '@chakra-ui/react';
import { CheckIcon } from '@chakra-ui/icons';

export interface PlanStep {
  id: number;
  content: string;
  status?: 'pending' | 'current' | 'done';
}

export interface ParsedPlan {
  summary?: string;
  steps: PlanStep[];
  status?: string;
  header?: string;
}

/**
 * Parse plan tool output / assistant markdown into a ParsedPlan.
 * Returns null if no plan detected.
 *
 * Expected markdown (as returned by backend plan_to_markdown):
 *   ## Plan: <summary>
 *
 *   1. step one
 *   2. step two
 *
 *   *Durum: draft*
 */
export function parsePlan(text: string): ParsedPlan | null {
  if (!text || !text.includes('##')) return null;

  // Match "## Plan" or "## Plan: summary"
  const planHeader = /^##\s*Plan(?::\s*(.+))?$/im;
  const headerMatch = planHeader.exec(text);
  if (!headerMatch) return null;

  const summary = (headerMatch[1] || '').trim();

  // Grab everything after the header
  const afterHeader = text.slice(headerMatch.index + headerMatch[0].length);

  const stepRegex = /^\s*(\d+)\.\s+(.+?)\s*$/gm;
  const steps: PlanStep[] = [];
  let m: RegExpExecArray | null;
  while ((m = stepRegex.exec(afterHeader)) !== null) {
    const id = parseInt(m[1], 10);
    const content = m[2].trim();
    if (content) steps.push({ id, content, status: 'pending' });
  }

  if (steps.length === 0) return null;

  // Try to read status ("*Durum: ...*")
  const statusMatch = /\*\s*Durum:\s*([^*]+?)\s*\*/i.exec(text);
  const status = statusMatch ? statusMatch[1].trim() : undefined;

  // Prefix before the plan (e.g. "Plan olusturuldu (3 adim).")
  const header = text.slice(0, headerMatch.index).trim();

  return { summary, steps, status, header: header || undefined };
}

const STATUS_COLORS: Record<string, string> = {
  draft: 'gray',
  approved: 'green',
  executing: 'blue',
  completed: 'purple',
  cancelled: 'red',
};

export default function PlanCard({ plan }: { plan: ParsedPlan }) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  const scheme = plan.status ? STATUS_COLORS[plan.status.toLowerCase()] || 'gray' : 'gray';

  return (
    <Box
      my={2}
      borderRadius="xl"
      border="1px solid"
      borderColor={isDark ? 'blue.700' : 'blue.200'}
      bg={isDark ? 'gray.800' : 'blue.50'}
      overflow="hidden"
      boxShadow="sm"
    >
      <Flex
        align="center"
        justify="space-between"
        px={4}
        py={2.5}
        bg={isDark ? 'blue.900' : 'blue.100'}
        borderBottom="1px solid"
        borderColor={isDark ? 'blue.700' : 'blue.200'}
      >
        <HStack spacing={2}>
          <Text fontSize="md">📋</Text>
          <Text fontSize="sm" fontWeight="bold" color={isDark ? 'blue.100' : 'blue.800'}>
            {plan.summary ? `Plan: ${plan.summary}` : 'Plan'}
          </Text>
        </HStack>
        <HStack spacing={2}>
          <Badge fontSize="2xs" colorScheme="blue" variant="subtle">
            {plan.steps.length} adim
          </Badge>
          {plan.status && (
            <Badge fontSize="2xs" colorScheme={scheme} variant="solid" textTransform="lowercase">
              {plan.status}
            </Badge>
          )}
        </HStack>
      </Flex>

      <Box px={4} py={3}>
        {plan.steps.map((step, idx) => {
          const isLast = idx === plan.steps.length - 1;
          const isDone = step.status === 'done';
          const isCurrent = step.status === 'current';
          return (
            <Flex key={step.id} position="relative" pb={isLast ? 0 : 3}>
              {/* Connector line */}
              {!isLast && (
                <Box
                  position="absolute"
                  left="11px"
                  top="24px"
                  bottom={0}
                  w="2px"
                  bg={isDark ? 'gray.700' : 'blue.200'}
                />
              )}
              {/* Step badge */}
              <Flex
                w="24px"
                h="24px"
                borderRadius="full"
                bg={isDone ? 'green.500' : isCurrent ? 'blue.500' : isDark ? 'gray.700' : 'white'}
                border="2px solid"
                borderColor={
                  isDone
                    ? 'green.500'
                    : isCurrent
                    ? 'blue.500'
                    : isDark
                    ? 'gray.600'
                    : 'blue.300'
                }
                color={isDone || isCurrent ? 'white' : isDark ? 'gray.300' : 'blue.600'}
                align="center"
                justify="center"
                fontSize="xs"
                fontWeight="bold"
                flexShrink={0}
                zIndex={1}
              >
                {isDone ? <CheckIcon boxSize={2.5} /> : step.id}
              </Flex>
              <Text
                ml={3}
                fontSize="sm"
                color={isDark ? 'gray.100' : 'gray.800'}
                lineHeight="24px"
                fontWeight={isCurrent ? 'semibold' : 'normal'}
              >
                {step.content}
              </Text>
            </Flex>
          );
        })}
      </Box>
    </Box>
  );
}
