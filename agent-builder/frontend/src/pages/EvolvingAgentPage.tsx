import { useState, useEffect } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Flex,
  IconButton,
  Button,
  Tabs,
  TabList,
  Tab,
  TabPanels,
  TabPanel,
  useColorMode,
} from '@chakra-ui/react';
import { ChevronRightIcon, ChevronLeftIcon, ViewIcon, StarIcon } from '@chakra-ui/icons';
import { AgentProvider, useAgentContext } from '../context/AgentContext';
import EvolvingAgentSidebar from '../components/EvolvingAgentSidebar';
import AssistantChat from '../components/AssistantChat';
import OntologyPanel from '../components/OntologyPanel';
import BatchMonitor from '../components/BatchMonitor';
import NotificationBar from '../components/NotificationBar';
import PlanPanel from '../components/PlanPanel';
import ResourcesPanel from '../components/resources/ResourcesPanel';

function AgentPageInner() {
  const { agentId } = useParams<{ agentId?: string }>();
  const navigate = useNavigate();
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
          <Box flex={1} />
          {activeAgent && (
            <>
              <Button
                size="xs"
                leftIcon={<StarIcon />}
                colorScheme="purple"
                variant="outline"
                onClick={() => navigate(`/evolving/${activeAgent.agent_id}/wiki`)}
                mr={2}
              >
                Wiki (Sahne)
              </Button>
              <Button
                size="xs"
                leftIcon={<ViewIcon />}
                colorScheme="blue"
                variant="outline"
                onClick={() => navigate(`/evolving/${activeAgent.agent_id}/ecosystem`)}
              >
                Ecosystem
              </Button>
            </>
          )}
        </Flex>
      </Box>
      <NotificationBar />
      <Flex flex={1} overflow="hidden">
        <EvolvingAgentSidebar />

        <Flex flex={1} overflow="hidden">
          <AssistantChat />

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
              w={{ base: '420px', lg: '480px', xl: '560px', '2xl': '640px' }}
              direction="column"
              borderLeft="1px"
              borderColor={isDark ? 'gray.700' : 'gray.200'}
              overflow="hidden"
            >
              <Tabs size="sm" variant="enclosed" display="flex" flexDirection="column" h="100%">
                <TabList flexShrink={0}>
                  <Tab fontSize="xs">Plan</Tab>
                  <Tab fontSize="xs">Ontoloji</Tab>
                  <Tab fontSize="xs">Kaynaklar</Tab>
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
                    <ResourcesPanel />
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
