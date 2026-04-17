import { Box, HStack, Text, Badge, useColorMode, Wrap, WrapItem } from '@chakra-ui/react';
import { CheckCircleIcon, InfoIcon } from '@chakra-ui/icons';
import { makeAssistantToolUI } from '@assistant-ui/react';
import CompactToolWrapper from './CompactToolWrapper';

type OntologyArgs = {
  name?: string;
  label?: string;
  description?: string;
  parent?: string;
  properties?: Record<string, string>;
  subject?: string;
  object?: string;
  predicate?: string;
  rule?: string;
  constraint?: string;
};

function OntologyChangeCard({
  tone,
  title,
  args,
  result,
}: {
  tone: 'purple' | 'pink' | 'cyan' | 'orange';
  title: string;
  args?: OntologyArgs;
  result?: unknown;
}) {
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';
  const displayName = args?.name || args?.label || args?.predicate || args?.rule || args?.constraint || '';
  const subject = args?.subject;
  const object = args?.object;
  const description = args?.description;
  const message = typeof result === 'string' ? result : '';

  const summary =
    subject && object
      ? `${displayName || ''} (${subject} → ${object})`.trim()
      : displayName;

  const hasDetails = Boolean(description) || Boolean(args?.properties && Object.keys(args.properties).length > 0) || (message && !displayName);

  return (
    <CompactToolWrapper
      toolName={title}
      icon={CheckCircleIcon}
      colorScheme={tone}
      summary={summary || (message ? 'guncellendi' : 'eklendi')}
    >
      {hasDetails && (
        <Box px={3} py={2}>
          {description && (
            <Text fontSize="xs" color={isDark ? 'gray.300' : 'gray.600'} mb={2}>
              {description}
            </Text>
          )}
          {args?.properties && Object.keys(args.properties).length > 0 && (
            <Wrap spacing={1} mb={message ? 2 : 0}>
              {Object.entries(args.properties).map(([k, v]) => (
                <WrapItem key={k}>
                  <Badge variant="outline" fontSize="2xs">
                    {k}: {String(v)}
                  </Badge>
                </WrapItem>
              ))}
            </Wrap>
          )}
          {message && !displayName && (
            <Text fontSize="xs" color={isDark ? 'gray.300' : 'gray.600'}>
              {message}
            </Text>
          )}
        </Box>
      )}
    </CompactToolWrapper>
  );
}

function OntologySnapshotCard({ result }: { result: unknown }) {
  let entityCount = 0;
  let relCount = 0;
  let ruleCount = 0;
  let constraintCount = 0;

  const tryExtract = (obj: Record<string, unknown>) => {
    const entities = (obj.entities ?? obj.entity_classes) as unknown;
    const rels = (obj.relationships ?? obj.predicates ?? obj.relationship_predicates) as unknown;
    const rules = (obj.rules ?? obj.inference_rules) as unknown;
    const cons = (obj.constraints ?? obj.constraint_list) as unknown;
    if (Array.isArray(entities)) entityCount = entities.length;
    if (Array.isArray(rels)) relCount = rels.length;
    if (Array.isArray(rules)) ruleCount = rules.length;
    if (Array.isArray(cons)) constraintCount = cons.length;
  };

  if (typeof result === 'string') {
    const trimmed = result.trim();
    if (trimmed.startsWith('{')) {
      try {
        tryExtract(JSON.parse(trimmed));
      } catch {
        /* ignore */
      }
    }
  } else if (result && typeof result === 'object') {
    tryExtract(result as Record<string, unknown>);
  }

  return (
    <CompactToolWrapper
      toolName="Ontoloji Ozeti"
      icon={InfoIcon}
      colorScheme="purple"
      summary={`${entityCount} entity · ${relCount} rel · ${ruleCount} rule · ${constraintCount} constraint`}
    >
      <Box px={3} py={2}>
        <HStack spacing={2} flexWrap="wrap">
          <Badge colorScheme="purple" variant="solid" px={2} py={1}>
            {entityCount} Entity
          </Badge>
          <Badge colorScheme="pink" variant="solid" px={2} py={1}>
            {relCount} Relationship
          </Badge>
          <Badge colorScheme="cyan" variant="solid" px={2} py={1}>
            {ruleCount} Rule
          </Badge>
          <Badge colorScheme="orange" variant="solid" px={2} py={1}>
            {constraintCount} Constraint
          </Badge>
        </HStack>
      </Box>
    </CompactToolWrapper>
  );
}

export const AddEntityClassToolUI = makeAssistantToolUI<OntologyArgs, unknown>({
  toolName: 'add_entity_class',
  render: ({ args, result }) => (
    <OntologyChangeCard tone="purple" title="Entity Eklendi" args={args} result={result} />
  ),
});

export const AddRelationshipPredicateToolUI = makeAssistantToolUI<OntologyArgs, unknown>({
  toolName: 'add_relationship_predicate',
  render: ({ args, result }) => (
    <OntologyChangeCard tone="pink" title="Relationship Eklendi" args={args} result={result} />
  ),
});

export const AddInferenceRuleToolUI = makeAssistantToolUI<OntologyArgs, unknown>({
  toolName: 'add_inference_rule',
  render: ({ args, result }) => (
    <OntologyChangeCard tone="cyan" title="Inference Rule Eklendi" args={args} result={result} />
  ),
});

export const AddConstraintToolUI = makeAssistantToolUI<OntologyArgs, unknown>({
  toolName: 'add_constraint',
  render: ({ args, result }) => (
    <OntologyChangeCard tone="orange" title="Constraint Eklendi" args={args} result={result} />
  ),
});

export const GetCurrentOntologyToolUI = makeAssistantToolUI<Record<string, unknown>, unknown>({
  toolName: 'get_current_ontology',
  render: ({ result }) => <OntologySnapshotCard result={result} />,
});
