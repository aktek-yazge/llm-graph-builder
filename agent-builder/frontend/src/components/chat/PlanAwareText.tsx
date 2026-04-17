import { Box } from '@chakra-ui/react';
import ReactMarkdown from 'react-markdown';
import type { TextMessagePartComponent } from '@assistant-ui/react';
import PlanCard, { parsePlan } from './PlanCard';

const PlanAwareText: TextMessagePartComponent = ({ text }) => {
  if (!text) return <Box as="span" color="gray.500">…</Box>;

  const plan = parsePlan(text);
  if (plan) {
    return (
      <Box>
        {plan.header && (
          <Box className="prose prose-sm dark:prose-invert" sx={{ maxWidth: 'none' }} mb={1}>
            <ReactMarkdown>{plan.header}</ReactMarkdown>
          </Box>
        )}
        <PlanCard plan={plan} />
      </Box>
    );
  }

  return (
    <Box className="prose prose-sm dark:prose-invert" sx={{ maxWidth: 'none' }}>
      <ReactMarkdown>{text}</ReactMarkdown>
    </Box>
  );
};

export default PlanAwareText;
