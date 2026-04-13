import { useState } from 'react';
import {
  Box,
  Flex,
  Text,
  IconButton,
  Tabs,
  TabList,
  TabPanels,
  TabPanel,
  Tab,
  Badge,
  useColorMode,
} from '@chakra-ui/react';
import { RepeatIcon } from '@chakra-ui/icons';
import { useAgentContext } from '../context/AgentContext';
import { hashColor } from '../services/evolvingApi';
import OntologyGraph from './OntologyGraph';
import DiscoveryQueue from './DiscoveryQueue';

export default function OntologyPanel() {
  const { activeAgent, ontology, refreshOntology, refreshDiscoveries } = useAgentContext();
  const { colorMode } = useColorMode();
  const isDark = colorMode === 'dark';

  if (!activeAgent) {
    return (
      <Flex align="center" justify="center" h="full" color="gray.400" p={4}>
        <Text fontSize="sm">Agent secin</Text>
      </Flex>
    );
  }

  const handleRefresh = () => {
    refreshOntology();
    refreshDiscoveries('pending');
  };

  const entities = ontology?.entity_classes || [];
  const relationships = ontology?.relationship_predicates || [];

  return (
    <Flex direction="column" h="full">
      {/* Header */}
      <Flex
        align="center"
        justify="space-between"
        px={4}
        py={2}
        borderBottom="1px"
        borderColor={isDark ? 'gray.700' : 'gray.200'}
      >
        <Text fontWeight="semibold" fontSize="sm">
          Ontoloji
        </Text>
        <IconButton
          aria-label="Yenile"
          icon={<RepeatIcon />}
          size="sm"
          variant="ghost"
          onClick={handleRefresh}
        />
      </Flex>

      {/* Stats */}
      {ontology && (
        <Box
          px={4}
          py={2}
          bg={isDark ? 'gray.800' : 'gray.50'}
          fontSize="xs"
          color="gray.500"
          borderBottom="1px"
          borderColor={isDark ? 'gray.700' : 'gray.200'}
        >
          {ontology.domain && <Text as="span" mr={3}>Domain: {ontology.domain}</Text>}
          <Text as="span">{entities.length} entity</Text>
          <Text as="span" mx={1}>|</Text>
          <Text as="span">{relationships.length} rel</Text>
        </Box>
      )}

      <DiscoveryQueue />

      {/* Tabs */}
      <Tabs size="sm" variant="line" flex={1} display="flex" flexDirection="column">
        <TabList px={4}>
          <Tab>Entity'ler</Tab>
          <Tab>Iliskiler</Tab>
          <Tab>Graf</Tab>
        </TabList>

        <TabPanels flex={1} overflowY="auto">
          {/* Entities */}
          <TabPanel>
            {entities.length === 0 ? (
              <Text fontSize="sm" color="gray.400">
                Henuz entity tanimlanmamis. Agent ile konusarak ekleyebilirsiniz.
              </Text>
            ) : (
              entities.map((e) => (
                <Box
                  key={e.name}
                  mb={2}
                  p={3}
                  borderRadius="lg"
                  border="1px"
                  borderColor={isDark ? 'gray.700' : 'gray.200'}
                >
                  <Flex align="center" gap={2} mb={1}>
                    <Box
                      w={3}
                      h={3}
                      borderRadius="full"
                      flexShrink={0}
                      bg={hashColor(e.name)}
                    />
                    <Text fontWeight="semibold" fontSize="sm">
                      {e.name}
                    </Text>
                    {e.parent && (
                      <Text fontSize="xs" color="gray.400">
                        extends {e.parent}
                      </Text>
                    )}
                  </Flex>
                  {e.description && (
                    <Text fontSize="xs" color="gray.500" mb={1}>
                      {e.description}
                    </Text>
                  )}
                  {e.properties.length > 0 && (
                    <Flex gap={1} mt={1} flexWrap="wrap">
                      {e.properties.map((p) => (
                        <Badge
                          key={p.name}
                          fontSize="10px"
                          colorScheme={p.constraint === 'required' ? 'red' : 'gray'}
                          variant="subtle"
                        >
                          {p.name}: {p.type}
                        </Badge>
                      ))}
                    </Flex>
                  )}
                </Box>
              ))
            )}
          </TabPanel>

          {/* Relationships */}
          <TabPanel>
            {relationships.length === 0 ? (
              <Text fontSize="sm" color="gray.400">
                Henuz iliski tanimlanmamis.
              </Text>
            ) : (
              relationships.map((r) => (
                <Box
                  key={r.name}
                  mb={2}
                  p={3}
                  borderRadius="lg"
                  border="1px"
                  borderColor={isDark ? 'gray.700' : 'gray.200'}
                >
                  <Text fontWeight="semibold" fontSize="sm">
                    {r.name}
                  </Text>
                  <Flex align="center" gap={1} fontSize="xs" color="gray.500" mt={1}>
                    {r.source && (
                      <>
                        <Box w={2} h={2} borderRadius="full" bg={hashColor(r.source)} />
                        <Text>{r.source}</Text>
                        <Text mx={1}>-&gt;</Text>
                      </>
                    )}
                    {r.target && (
                      <>
                        <Box w={2} h={2} borderRadius="full" bg={hashColor(r.target)} />
                        <Text>{r.target}</Text>
                      </>
                    )}
                  </Flex>
                  {r.description && (
                    <Text fontSize="xs" color="gray.500" mt={1}>
                      {r.description}
                    </Text>
                  )}
                  {r.edge_properties.length > 0 && (
                    <Flex gap={1} mt={1} flexWrap="wrap">
                      {r.edge_properties.map((p) => (
                        <Badge key={p} fontSize="10px" variant="subtle" colorScheme="gray">
                          {p}
                        </Badge>
                      ))}
                    </Flex>
                  )}
                </Box>
              ))
            )}
          </TabPanel>

          {/* Graph */}
          <TabPanel>
            {ontology && <OntologyGraph ontology={ontology} />}
          </TabPanel>
        </TabPanels>
      </Tabs>
    </Flex>
  );
}
