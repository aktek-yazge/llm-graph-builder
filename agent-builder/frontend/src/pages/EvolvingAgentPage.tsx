import { useState, useEffect } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Box,
  Flex,
  IconButton,
  Tabs,
  TabList,
  Tab,
  TabPanels,
  TabPanel,
  useColorMode,
} from '@chakra-ui/react';
import { ChevronRightIcon, ChevronLeftIcon } from '@chakra-ui/icons';
import { AgentProvider, useAgentContext } from '../context/AgentContext';
import EvolvingAgentSidebar from '../components/EvolvingAgentSidebar';
import EvolvingAgentChat from '../components/EvolvingAgentChat';
import OntologyPanel from '../components/OntologyPanel';
import BatchMonitor from '../components/BatchMonitor';
import NotificationBar from '../components/NotificationBar';
import PlanPanel from '../components/PlanPanel';

function AgentPageInner() {
  const { agentId } = useParams<{ agentId?: string }>();
  const { activeAgent, selectAgent, loadAgents } = useAgentContext();
  const [rightPanelOpen, setRightPanelOpen] = useState(true);
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
        <Flex alignItems="center" gap={1} fontSize="xs" color="gray.500">
          <Link to="/dashboard" className="hover:text-indigo-600 transition-colors">
            Dashboard
          </Link>
          <span>/</span>
          <span style={{ color: isDark ? '#e2e8f0' : '#1e293b', fontWeight: 500 }}>
            Self-Evolving Agent
          </span>
        </Flex>
      </Box>
      <NotificationBar />
      <Flex flex={1} overflow="hidden">
        <EvolvingAgentSidebar />

        <Flex flex={1} overflow="hidden">
          <EvolvingAgentChat />

          <Box position="relative" flexShrink={0}>
            <IconButton
              aria-label={rightPanelOpen ? 'Paneli kapat' : 'Paneli ac'}
              icon={rightPanelOpen ? <ChevronRightIcon /> : <ChevronLeftIcon />}
              size="sm"
              variant="outline"
              bg={isDark ? 'gray.800' : 'white'}
              position="absolute"
              left="-12px"
              top="16px"
              zIndex={10}
              borderRadius="full"
              boxShadow="md"
              onClick={() => setRightPanelOpen(!rightPanelOpen)}
            />
          </Box>

          {rightPanelOpen && (
            <Flex
              w="96"
              direction="column"
              borderLeft="1px"
              borderColor={isDark ? 'gray.700' : 'gray.200'}
              overflow="hidden"
            >
              <Tabs size="sm" variant="enclosed" display="flex" flexDirection="column" h="100%">
                <TabList flexShrink={0}>
                  <Tab fontSize="xs">Plan</Tab>
                  <Tab fontSize="xs">Ontoloji</Tab>
                  <Tab fontSize="xs">Batch</Tab>
                </TabList>
                <TabPanels flex={1} overflow="hidden">
                  <TabPanel p={0} h="100%">
                    <PlanPanel />
                  </TabPanel>
                  <TabPanel p={0} h="100%" overflowY="auto">
                    <OntologyPanel />
                  </TabPanel>
                  <TabPanel p={0} h="100%">
                    <BatchMonitor />
                  </TabPanel>
                </TabPanels>
              </Tabs>
            </Flex>
          )}
        </Flex>
      </Flex>
    </Flex>
  );
}

export default function EvolvingAgentPage() {
  return (
    <AgentProvider>
      <AgentPageInner />
    </AgentProvider>
  );
}
