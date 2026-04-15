import React, { useState } from 'react';
import {
  Box,
  VStack,
  HStack,
  Text,
  Button,
  IconButton,
  Badge,
  Progress,
  Input,
  Editable,
  EditablePreview,
  EditableInput,
  Tooltip,
  Divider,
  Collapse,
  useDisclosure,
} from '@chakra-ui/react';
import {
  CheckCircleIcon,
  EditIcon,
  DeleteIcon,
  AddIcon,
  ChevronUpIcon,
  ChevronDownIcon,
} from '@chakra-ui/icons';
import { useAgentContext } from '../context/AgentContext';
import type { PlanStep } from '../services/evolvingApi';

const statusColor: Record<string, string> = {
  draft: 'yellow',
  approved: 'blue',
  executing: 'purple',
  completed: 'green',
};

const statusLabel: Record<string, string> = {
  draft: 'Taslak',
  approved: 'Onaylandi',
  executing: 'Uygulanıyor',
  completed: 'Tamamlandi',
};

export default function PlanPanel() {
  const {
    mode,
    plan,
    todos,
    switchMode,
    updatePlanStep,
    addPlanStep,
    removePlanStep,
    activeAgent,
    isStreaming,
  } = useAgentContext();

  const { isOpen: todosOpen, onToggle: toggleTodos } = useDisclosure({ defaultIsOpen: true });
  const [newStepText, setNewStepText] = useState('');

  if (!activeAgent) return null;

  const steps = plan?.steps ?? [];
  const planStatus = plan?.status ?? 'draft';
  const completedTodos = todos.filter((t) => t.status === 'completed').length;
  const todoProgress = todos.length > 0 ? (completedTodos / todos.length) * 100 : 0;

  const handleApply = () => switchMode('agent');
  const handleBackToPlan = () => switchMode('plan');

  const handleAddStep = () => {
    if (!newStepText.trim()) return;
    const lastId = steps.length > 0 ? steps[steps.length - 1].id : 0;
    addPlanStep(lastId, newStepText.trim());
    setNewStepText('');
  };

  return (
    <Box
      h="100%"
      bg="gray.50"
      _dark={{ bg: 'gray.800' }}
      borderLeft="1px solid"
      borderColor="gray.200"
      display="flex"
      flexDirection="column"
    >
      {/* Header */}
      <HStack px={4} py={3} borderBottom="1px solid" borderColor="gray.200" justify="space-between">
        <HStack spacing={2}>
          <Text fontWeight="bold" fontSize="sm">
            {mode === 'plan' ? 'Plan Mode' : 'Agent Mode'}
          </Text>
          <Badge colorScheme={mode === 'plan' ? 'yellow' : 'green'} fontSize="xs">
            {mode === 'plan' ? 'Readonly' : 'Uygulama'}
          </Badge>
        </HStack>
        {mode === 'plan' && plan && planStatus === 'draft' && (
          <Button
            size="xs"
            colorScheme="green"
            leftIcon={<CheckCircleIcon />}
            onClick={handleApply}
            isDisabled={isStreaming || steps.length === 0}
          >
            Uygula
          </Button>
        )}
        {mode === 'agent' && (
          <Button size="xs" variant="outline" onClick={handleBackToPlan} isDisabled={isStreaming}>
            Plana Don
          </Button>
        )}
      </HStack>

      {/* Plan Steps */}
      <Box flex={1} overflowY="auto" px={4} py={3}>
        {plan ? (
          <VStack align="stretch" spacing={2}>
            <HStack justify="space-between">
              <Text fontSize="xs" fontWeight="semibold" color="gray.500" textTransform="uppercase">
                Plan {plan.summary ? `— ${plan.summary}` : ''}
              </Text>
              <Badge colorScheme={statusColor[planStatus]} fontSize="2xs">
                {statusLabel[planStatus] || planStatus}
              </Badge>
            </HStack>

            {steps.map((step: PlanStep) => (
              <HStack
                key={step.id}
                bg="white"
                _dark={{ bg: 'gray.700' }}
                px={3}
                py={2}
                borderRadius="md"
                borderLeft="3px solid"
                borderLeftColor="blue.400"
                spacing={2}
              >
                <Text fontSize="xs" color="gray.400" fontWeight="bold" minW="20px">
                  {step.id}.
                </Text>
                {mode === 'plan' && planStatus === 'draft' ? (
                  <Editable
                    defaultValue={step.content}
                    flex={1}
                    fontSize="sm"
                    onSubmit={(val) => {
                      if (val !== step.content) updatePlanStep(step.id, val);
                    }}
                  >
                    <EditablePreview cursor="pointer" _hover={{ bg: 'gray.50', _dark: { bg: 'gray.600' } }} />
                    <EditableInput />
                  </Editable>
                ) : (
                  <Text flex={1} fontSize="sm">
                    {step.content}
                  </Text>
                )}
                {mode === 'plan' && planStatus === 'draft' && (
                  <Tooltip label="Adimi kaldir">
                    <IconButton
                      aria-label="Remove step"
                      icon={<DeleteIcon />}
                      size="xs"
                      variant="ghost"
                      colorScheme="red"
                      onClick={() => removePlanStep(step.id)}
                    />
                  </Tooltip>
                )}
              </HStack>
            ))}

            {mode === 'plan' && planStatus === 'draft' && (
              <HStack mt={1}>
                <Input
                  size="sm"
                  placeholder="Yeni adim ekle..."
                  value={newStepText}
                  onChange={(e) => setNewStepText(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleAddStep()}
                />
                <IconButton
                  aria-label="Add step"
                  icon={<AddIcon />}
                  size="sm"
                  colorScheme="blue"
                  onClick={handleAddStep}
                  isDisabled={!newStepText.trim()}
                />
              </HStack>
            )}
          </VStack>
        ) : (
          <Text fontSize="sm" color="gray.500" textAlign="center" mt={8}>
            Henuz plan yok. Agent ile konusarak plan olusturun.
          </Text>
        )}

        {/* Todos Section */}
        {todos.length > 0 && (
          <>
            <Divider my={3} />
            <VStack align="stretch" spacing={2}>
              <HStack cursor="pointer" onClick={toggleTodos} justify="space-between">
                <HStack spacing={2}>
                  <Text fontSize="xs" fontWeight="semibold" color="gray.500" textTransform="uppercase">
                    Gorevler
                  </Text>
                  <Badge colorScheme="blue" fontSize="2xs">
                    {completedTodos}/{todos.length}
                  </Badge>
                </HStack>
                {todosOpen ? <ChevronUpIcon /> : <ChevronDownIcon />}
              </HStack>

              <Progress
                value={todoProgress}
                size="xs"
                colorScheme="green"
                borderRadius="full"
                hasStripe={todoProgress < 100}
                isAnimated={todoProgress < 100}
              />

              <Collapse in={todosOpen}>
                <VStack align="stretch" spacing={1}>
                  {todos.map((todo, i) => (
                    <HStack key={i} px={2} py={1} fontSize="xs">
                      <Text
                        color={
                          todo.status === 'completed'
                            ? 'green.500'
                            : todo.status === 'in_progress'
                            ? 'blue.500'
                            : 'gray.400'
                        }
                      >
                        {todo.status === 'completed' ? '✓' : todo.status === 'in_progress' ? '▶' : '○'}
                      </Text>
                      <Text
                        flex={1}
                        textDecoration={todo.status === 'completed' ? 'line-through' : 'none'}
                        color={todo.status === 'completed' ? 'gray.400' : undefined}
                      >
                        {todo.content}
                      </Text>
                    </HStack>
                  ))}
                </VStack>
              </Collapse>
            </VStack>
          </>
        )}
      </Box>
    </Box>
  );
}
