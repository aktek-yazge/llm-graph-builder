import { Box, Text, Spinner, HStack } from '@chakra-ui/react';
import { makeAssistantToolUI } from '@assistant-ui/react';
import PlanCard, { parsePlan, type PlanStep, type ParsedPlan } from '../PlanCard';

type PlanArgs = {
  summary?: string;
  steps?: string[];
  step_id?: number;
  content?: string;
  after_step_id?: number;
};

function argsToPlan(args: PlanArgs | undefined): ParsedPlan | null {
  if (!args) return null;
  const raw = args.steps;
  if (!raw || raw.length === 0) return null;
  const steps: PlanStep[] = raw.map((content, idx) => ({
    id: idx + 1,
    content,
    status: 'pending',
  }));
  return { summary: args.summary, steps, status: 'draft' };
}

function renderPlanResult(label: string, result: unknown, args: PlanArgs | undefined) {
  const text = typeof result === 'string' ? result : result ? JSON.stringify(result) : '';
  const plan = text ? parsePlan(text) : argsToPlan(args);

  if (!plan) {
    return (
      <Box my={2} px={3} py={2} borderRadius="md" bg="blue.50" borderLeft="3px solid" borderColor="blue.300">
        <HStack spacing={2}>
          <Spinner size="xs" color="blue.500" />
          <Text fontSize="xs" color="blue.600" fontWeight="medium">
            {label}…
          </Text>
        </HStack>
      </Box>
    );
  }

  return (
    <Box>
      <Text fontSize="2xs" color="blue.500" fontWeight="bold" textTransform="uppercase" letterSpacing="wider" mb={1}>
        {label}
      </Text>
      <PlanCard plan={plan} />
    </Box>
  );
}

export const CreatePlanToolUI = makeAssistantToolUI<PlanArgs, string>({
  toolName: 'create_plan',
  render: ({ result, args }) => renderPlanResult('Plan Olusturuldu', result, args),
});

export const UpdatePlanStepToolUI = makeAssistantToolUI<PlanArgs, string>({
  toolName: 'update_plan_step',
  render: ({ result, args }) => renderPlanResult('Adim Guncellendi', result, args),
});

export const AddPlanStepToolUI = makeAssistantToolUI<PlanArgs, string>({
  toolName: 'add_plan_step',
  render: ({ result, args }) => renderPlanResult('Adim Eklendi', result, args),
});

export const RemovePlanStepToolUI = makeAssistantToolUI<PlanArgs, string>({
  toolName: 'remove_plan_step',
  render: ({ result, args }) => renderPlanResult('Adim Silindi', result, args),
});

export const GetCurrentPlanToolUI = makeAssistantToolUI<PlanArgs, string>({
  toolName: 'get_current_plan',
  render: ({ result, args }) => renderPlanResult('Mevcut Plan', result, args),
});
