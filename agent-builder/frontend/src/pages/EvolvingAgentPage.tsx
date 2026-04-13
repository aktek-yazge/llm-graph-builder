import { useState } from 'react';
import {
  Box,
  Flex,
  IconButton,
  useColorMode,
} from '@chakra-ui/react';
import { ChevronRightIcon, ChevronLeftIcon } from '@chakra-ui/icons';
import { AgentProvider } from '../context/AgentContext';
import EvolvingAgentSidebar from '../components/EvolvingAgentSidebar';
import EvolvingAgentChat from '../components/EvolvingAgentChat';
import OntologyPanel from '../components/OntologyPanel';
import BatchMonitor from '../components/BatchMonitor';
import NotificationBar from '../components/NotificationBar';

function AgentPageContent() {
  const [rightPanelOpen, setRightPanelOpen] = useState(true);
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  return (
    <AgentProvider>
      <Flex direction="column" h="calc(100vh - 48px)">
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
                <Box flex={1} overflowY="auto">
                  <OntologyPanel />
                </Box>
                <BatchMonitor />
              </Flex>
            )}
          </Flex>
        </Flex>
      </Flex>
    </AgentProvider>
  );
}

export default function EvolvingAgentPage() {
  return <AgentPageContent />;
}
