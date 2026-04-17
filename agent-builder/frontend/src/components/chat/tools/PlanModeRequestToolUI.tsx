import { useState } from 'react';
import {
  Box,
  Button,
  HStack,
  Text,
  Badge,
  useColorMode,
} from '@chakra-ui/react';
import { CheckIcon, CloseIcon, EditIcon } from '@chakra-ui/icons';
import { makeAssistantToolUI } from '@assistant-ui/react';
import CompactToolWrapper from './CompactToolWrapper';
import { useAgentContext } from '../../../context/AgentContext';

type PlanModeArgs = {
  reason?: string;
  topic?: string;
};

type Decision = 'idle' | 'approving' | 'rejecting' | 'approved' | 'rejected';

function PlanModeRequestCard({ args }: { args?: PlanModeArgs }) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const { mode, respondPlanRequest } = useAgentContext();
  const [decision, setDecision] = useState<Decision>('idle');

  const reason = args?.reason ?? '';
  const topic = args?.topic || (reason ? reason.slice(0, 60) : 'Plan tartismasi');

  // If mode already flipped to plan, treat the request as approved (handles refresh).
  const effectiveDecision: Decision =
    decision !== 'idle' ? decision : mode === 'plan' ? 'approved' : 'idle';

  const handleApprove = async () => {
    setDecision('approving');
    try {
      await respondPlanRequest('approve', reason, topic);
      setDecision('approved');
    } catch {
      setDecision('idle');
    }
  };

  const handleReject = async () => {
    setDecision('rejecting');
    try {
      await respondPlanRequest('reject', reason, topic);
      setDecision('rejected');
    } catch {
      setDecision('idle');
    }
  };

  const isBusy = decision === 'approving' || decision === 'rejecting';
  const isResolved = effectiveDecision === 'approved' || effectiveDecision === 'rejected';

  const summary =
    effectiveDecision === 'approved'
      ? `Onaylandi · ${topic}`
      : effectiveDecision === 'rejected'
      ? `Reddedildi · ${topic}`
      : `Izin bekleniyor · ${topic}`;

  const colorScheme =
    effectiveDecision === 'approved'
      ? 'green'
      : effectiveDecision === 'rejected'
      ? 'gray'
      : 'purple';

  const statusBadge =
    effectiveDecision === 'approved' ? (
      <Badge colorScheme="green" variant="subtle" fontSize="2xs">onaylandi</Badge>
    ) : effectiveDecision === 'rejected' ? (
      <Badge colorScheme="gray" variant="subtle" fontSize="2xs">reddedildi</Badge>
    ) : (
      <Badge colorScheme="purple" variant="subtle" fontSize="2xs">izin bekliyor</Badge>
    );

  return (
    <CompactToolWrapper
      toolName="Plan Modu Istegi"
      icon={EditIcon}
      colorScheme={colorScheme}
      status={isBusy ? 'running' : 'done'}
      summary={summary}
      badges={statusBadge}
      defaultOpen={!isResolved}
    >
      <Box px={3} py={3}>
        <Text fontSize="xs" color={isDark ? 'gray.400' : 'gray.500'} fontWeight="semibold" mb={1}>
          Konu
        </Text>
        <Text fontSize="sm" color={isDark ? 'gray.100' : 'gray.800'} mb={3}>
          {topic}
        </Text>

        {reason && reason !== topic && (
          <>
            <Text fontSize="xs" color={isDark ? 'gray.400' : 'gray.500'} fontWeight="semibold" mb={1}>
              Neden
            </Text>
            <Text fontSize="sm" color={isDark ? 'gray.200' : 'gray.700'} mb={3}>
              {reason}
            </Text>
          </>
        )}

        {!isResolved ? (
          <HStack spacing={2}>
            <Button
              size="xs"
              colorScheme="green"
              leftIcon={<CheckIcon />}
              onClick={handleApprove}
              isLoading={decision === 'approving'}
              isDisabled={isBusy}
            >
              Plan Modunu Ac
            </Button>
            <Button
              size="xs"
              colorScheme="gray"
              variant="outline"
              leftIcon={<CloseIcon />}
              onClick={handleReject}
              isLoading={decision === 'rejecting'}
              isDisabled={isBusy}
            >
              Gerek Yok
            </Button>
          </HStack>
        ) : (
          <Text fontSize="xs" color={isDark ? 'gray.400' : 'gray.600'} fontStyle="italic">
            {effectiveDecision === 'approved'
              ? 'Plan modu acildi. Sonraki mesajinizda agent plan tool\u2019larina erisecek.'
              : 'Mevcut agent modunda devam ediliyor.'}
          </Text>
        )}
      </Box>
    </CompactToolWrapper>
  );
}

export const RequestPlanModeToolUI = makeAssistantToolUI<PlanModeArgs, unknown>({
  toolName: 'request_plan_mode',
  render: ({ args }) => <PlanModeRequestCard args={args} />,
});
