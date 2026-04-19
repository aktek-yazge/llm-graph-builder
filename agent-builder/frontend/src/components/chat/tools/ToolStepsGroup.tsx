import { useState, useEffect } from 'react';
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
import {
  ChevronDownIcon,
  ChevronUpIcon,
  SettingsIcon,
  AttachmentIcon,
  ViewIcon,
  CheckCircleIcon,
  EditIcon,
  TriangleUpIcon,
  TimeIcon,
  InfoIcon,
} from '@chakra-ui/icons';
import { makeAssistantToolUI } from '@assistant-ui/react';
import type { ToolStepInfo } from '../../../runtime/AgentRuntimeProvider';

type ElementType = React.ElementType;

const TOOL_META: Record<string, { label: string; icon: ElementType; color: string }> = {
  list_resources:             { label: 'Kaynaklar',            icon: AttachmentIcon,    color: 'teal' },
  delete_resource:            { label: 'Kaynak Silindi',       icon: AttachmentIcon,    color: 'red' },
  delete_all_resources:       { label: 'Tum Kaynaklar Silindi', icon: AttachmentIcon,   color: 'red' },
  run_ocr:                    { label: 'OCR',                  icon: ViewIcon,          color: 'orange' },
  ocr_and_analyze:            { label: 'OCR + Analiz',         icon: ViewIcon,          color: 'orange' },
  list_ocr_documents:         { label: 'OCR Belgeler',         icon: ViewIcon,          color: 'orange' },
  create_plan:                { label: 'Plan Olusturuldu',     icon: EditIcon,          color: 'blue' },
  update_plan_step:           { label: 'Adim Guncellendi',     icon: EditIcon,          color: 'blue' },
  add_plan_step:              { label: 'Adim Eklendi',         icon: EditIcon,          color: 'blue' },
  remove_plan_step:           { label: 'Adim Silindi',         icon: EditIcon,          color: 'blue' },
  add_entity_class:           { label: 'Entity Eklendi',       icon: CheckCircleIcon,   color: 'purple' },
  add_relationship_predicate: { label: 'Relationship Eklendi', icon: CheckCircleIcon,   color: 'pink' },
  add_inference_rule:         { label: 'Rule Eklendi',         icon: CheckCircleIcon,   color: 'cyan' },
  add_constraint:             { label: 'Constraint Eklendi',   icon: CheckCircleIcon,   color: 'orange' },
  get_current_ontology:       { label: 'Ontoloji Ozeti',       icon: InfoIcon,          color: 'purple' },
  start_batch_processing:     { label: 'Batch Islemi',         icon: TriangleUpIcon,    color: 'green' },
  get_batch_progress:         { label: 'Batch Ilerleme',       icon: TimeIcon,          color: 'blue' },
  request_plan_mode:          { label: 'Plan Modu Istegi',     icon: EditIcon,          color: 'purple' },
};

function getMeta(toolName: string) {
  return TOOL_META[toolName] ?? { label: toolName, icon: SettingsIcon, color: 'gray' };
}

function stepSummary(step: ToolStepInfo): string {
  if (step.result) {
    const text = typeof step.result === 'string' ? step.result : '';
    const firstLine = text.split('\n')[0] || '';
    return firstLine.slice(0, 60);
  }
  if (step.args && Object.keys(step.args).length > 0) {
    const vals = Object.values(step.args)
      .filter((v) => typeof v === 'string' || typeof v === 'number')
      .map(String)
      .join(', ');
    return vals.slice(0, 60);
  }
  return '';
}

function StepRow({ step, isDark }: { step: ToolStepInfo; isDark: boolean }) {
  const meta = getMeta(step.toolName);
  const baseColor = isDark ? `${meta.color}.300` : `${meta.color}.600`;
  const summary = stepSummary(step);

  return (
    <Flex
      align="center"
      px={3}
      py={1}
      _hover={{ bg: isDark ? 'gray.750' : 'gray.50' }}
    >
      <HStack spacing={2} flex={1} minW={0}>
        <Icon as={meta.icon} boxSize={2.5} color={baseColor} />
        <Text fontSize="2xs" fontWeight="semibold" color={baseColor} flexShrink={0}>
          {meta.label}
        </Text>
        {summary && (
          <Text
            fontSize="2xs"
            color={isDark ? 'gray.500' : 'gray.500'}
            noOfLines={1}
            flex={1}
            minW={0}
          >
            {summary}
          </Text>
        )}
      </HStack>
      {step.status === 'running' ? (
        <Spinner size="xs" color={`${meta.color}.400`} />
      ) : (
        <Icon as={CheckCircleIcon} boxSize={2.5} color="green.400" />
      )}
    </Flex>
  );
}

interface ToolStepsGroupProps {
  steps: ToolStepInfo[];
  allDone: boolean;
}

function ToolStepsGroupInner({ steps, allDone }: ToolStepsGroupProps) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const [open, setOpen] = useState(!allDone);

  useEffect(() => {
    if (allDone) setOpen(false);
  }, [allDone]);

  const runningCount = steps.filter((s) => s.status === 'running').length;
  const doneCount = steps.length - runningCount;

  const uniqueLabels = [...new Set(steps.map((s) => getMeta(s.toolName).label))];
  const labelPreview = uniqueLabels.slice(0, 3).join(', ') + (uniqueLabels.length > 3 ? '...' : '');

  const headerText = allDone
    ? `${steps.length} islem tamamlandi`
    : `${doneCount}/${steps.length} islem...`;

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
          <Icon as={SettingsIcon} boxSize={3} color={isDark ? 'blue.300' : 'blue.600'} />
          <Text fontSize="xs" fontWeight="semibold" color={isDark ? 'blue.300' : 'blue.600'} flexShrink={0}>
            {headerText}
          </Text>
          <Text
            fontSize="xs"
            color={isDark ? 'gray.500' : 'gray.500'}
            noOfLines={1}
            flex={1}
            minW={0}
          >
            {labelPreview}
          </Text>
        </HStack>
        <HStack spacing={1.5} flexShrink={0}>
          <Badge colorScheme={allDone ? 'green' : 'blue'} variant="subtle" fontSize="2xs">
            {steps.length}
          </Badge>
          {!allDone && <Spinner size="xs" color="blue.400" />}
          <Icon
            as={open ? ChevronUpIcon : ChevronDownIcon}
            boxSize={3.5}
            color={isDark ? 'gray.500' : 'gray.400'}
          />
        </HStack>
      </Flex>
      <Collapse in={open} animateOpacity>
        <Box borderTop="1px solid" borderColor={isDark ? 'gray.700' : 'gray.100'} py={1}>
          {steps.map((step) => (
            <StepRow key={step.id} step={step} isDark={isDark} />
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}

export const ToolStepsUI = makeAssistantToolUI<{ steps: ToolStepInfo[] }, unknown>({
  toolName: '_tool_steps',
  render: ({ args, status }) => {
    const steps: ToolStepInfo[] = (args as { steps?: ToolStepInfo[] })?.steps ?? [];
    const allDone = status?.type !== 'running';
    return <ToolStepsGroupInner steps={steps} allDone={allDone} />;
  },
});

export default ToolStepsGroupInner;
