import { useEffect } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Flex,
  IconButton,
  useColorMode,
} from '@chakra-ui/react';
import { ArrowBackIcon } from '@chakra-ui/icons';
import { ReactFlowProvider } from '@xyflow/react';
import { AgentProvider, useAgentContext } from '../context/AgentContext';
import WorkflowCanvas from '../components/workflow/Canvas';
import NotificationBar from '../components/NotificationBar';

function WorkflowPageInner() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const { activeAgent, selectAgent, loadAgents, activeWorkflowId } = useAgentContext();
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  useEffect(() => {
    if (agentId && activeAgent?.agent_id !== agentId) {
      loadAgents().then(() => selectAgent(agentId));
    }
  }, [agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Flex direction="column" h="100%">
      <Box
        px={3}
        py={1.5}
        borderBottom="1px"
        borderColor={isDark ? 'gray.700' : 'gray.100'}
        bg={isDark ? 'gray.800' : 'gray.50'}
        flexShrink={0}
      >
        <Flex alignItems="center" gap={2} fontSize="xs" color="gray.500">
          <IconButton
            aria-label="Geri"
            icon={<ArrowBackIcon />}
            size="xs"
            variant="ghost"
            onClick={() => navigate(agentId ? `/agents/${agentId}` : '/agents')}
          />
          <Link to="/dashboard" className="hover:text-indigo-600 transition-colors">
            Dashboard
          </Link>
          <span>/</span>
          <Link
            to={agentId ? `/agents/${agentId}` : '/agents'}
            className="hover:text-indigo-600 transition-colors"
          >
            {activeAgent?.name || 'Agent'}
          </Link>
          <span>/</span>
          <span style={{ color: isDark ? '#e2e8f0' : '#1e293b', fontWeight: 600 }}>
            Workflow Editor
          </span>
        </Flex>
      </Box>
      <NotificationBar />
      <Box flex={1} overflow="hidden">
        <ReactFlowProvider key={activeWorkflowId || 'default'}>
          <WorkflowCanvas workflowId={activeWorkflowId} />
        </ReactFlowProvider>
      </Box>
    </Flex>
  );
}

export default function WorkflowPage() {
  return (
    <AgentProvider>
      <WorkflowPageInner />
    </AgentProvider>
  );
}
