import { useState, useEffect, type ReactElement } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Box,
  Flex,
  Text,
  Tooltip,
  Badge,
} from '@chakra-ui/react';
import {
  MessageSquare,
  ListChecks,
  Network,
  Paperclip,
  Layers,
  Workflow,
  BookOpen,
  Cpu,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { AgentProvider, useAgentContext } from '../context/AgentContext';
import { updateAgentModel } from '../services/evolvingApi';
import EvolvingAgentSidebar from '../components/EvolvingAgentSidebar';
import AssistantChat from '../components/AssistantChat';
import OntologyPanel from '../components/OntologyPanel';
import BatchMonitor from '../components/BatchMonitor';
import NotificationBar from '../components/NotificationBar';
import PlanPanel from '../components/PlanPanel';
import ResourcesPanel from '../components/resources/ResourcesPanel';
import WorkflowPanel from '../components/workflow/WorkflowPanel';
import WikiPanel from '../components/wiki/WikiPanel';

const AVAILABLE_MODELS = [
  { provider: 'openai', model: 'gpt-5.4', label: 'GPT-5.4' },
  { provider: 'openai', model: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
  { provider: 'anthropic', model: 'claude-sonnet-4-6-20250414', label: 'Claude Sonnet 4.6' },
  { provider: 'anthropic', model: 'claude-opus-4-7-20250414', label: 'Claude Opus 4.7' },
  { provider: 'google', model: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
  { provider: 'google', model: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
];

type Section = 'chat' | 'plan' | 'ontology' | 'resources' | 'batch' | 'workflow' | 'wiki';

interface NavItem {
  id: Section;
  label: string;
  icon: LucideIcon;
  separator?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'chat', label: 'Sohbet', icon: MessageSquare },
  { id: 'plan', label: 'Plan', icon: ListChecks },
  { id: 'ontology', label: 'Ontoloji', icon: Network },
  { id: 'resources', label: 'Kaynaklar', icon: Paperclip },
  { id: 'batch', label: 'Batch', icon: Layers },
  { id: 'workflow', label: 'Workflow', icon: Workflow, separator: true },
  { id: 'wiki', label: 'Wiki', icon: BookOpen },
];

function NavSidebar({
  activeSection,
  onSelect,
}: {
  activeSection: Section;
  onSelect: (s: Section) => void;
}) {
  return (
    <Flex
      direction="column"
      w="56px"
      flexShrink={0}
      borderRight="1px"
      borderColor="border.subtle"
      bg="surface.primary"
      h="100%"
      overflowY="auto"
      py={2}
      sx={{ '&::-webkit-scrollbar': { display: 'none' } }}
    >
      {NAV_ITEMS.map((item) => {
        const isActive = activeSection === item.id;
        const Icon = item.icon;

        return (
          <Box key={item.id}>
            {item.separator && (
              <Box mx={4} my={1.5} borderTop="1px" borderColor="border.subtle" />
            )}
            <Tooltip label={item.label} placement="right" fontSize="xs" hasArrow>
              <Flex
                direction="column"
                align="center"
                justify="center"
                w="100%"
                py={2.5}
                px={1}
                cursor="pointer"
                position="relative"
                color={isActive ? 'text.primary' : 'text.quaternary'}
                _hover={{
                  color: isActive ? 'text.primary' : 'text.secondary',
                }}
                transition="color 0.15s"
                onClick={() => onSelect(item.id)}
                role="button"
                tabIndex={0}
              >
                <Icon size={18} strokeWidth={1.5} />
                <Text
                  fontSize="10px"
                  mt={1}
                  fontWeight={isActive ? '600' : '400'}
                  lineHeight="1"
                  letterSpacing="-0.01em"
                >
                  {item.label}
                </Text>
                {isActive && (
                  <Box
                    position="absolute"
                    bottom="4px"
                    w="4px"
                    h="4px"
                    borderRadius="full"
                    bg="brand.500"
                  />
                )}
              </Flex>
            </Tooltip>
          </Box>
        );
      })}
      <Box flex={1} />
    </Flex>
  );
}

function AgentPageInner() {
  const { agentId } = useParams<{ agentId?: string }>();
  const { activeAgent, selectAgent, loadAgents, mode } = useAgentContext();
  const [activeSection, setActiveSection] = useState<Section>('chat');
  const [showModelModal, setShowModelModal] = useState(false);
  const [selectedModel, setSelectedModel] = useState('');
  const [savingModel, setSavingModel] = useState(false);

  useEffect(() => {
    if (agentId && activeAgent?.agent_id !== agentId) {
      loadAgents().then(() => selectAgent(agentId));
    }
  }, [agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  const currentModelLabel = (() => {
    if (!activeAgent?.llm_provider && !activeAgent?.llm_model) return 'Varsayilan';
    const found = AVAILABLE_MODELS.find(
      (m) => m.provider === activeAgent?.llm_provider && m.model === activeAgent?.llm_model,
    );
    return found ? found.label : activeAgent?.llm_model || 'Varsayilan';
  })();

  const openModelModal = () => {
    const key =
      activeAgent?.llm_provider && activeAgent?.llm_model
        ? `${activeAgent.llm_provider}/${activeAgent.llm_model}`
        : '';
    setSelectedModel(key);
    setShowModelModal(true);
  };

  const handleSaveModel = async () => {
    if (!activeAgent) return;
    setSavingModel(true);
    try {
      const sel = AVAILABLE_MODELS.find(
        (m) => `${m.provider}/${m.model}` === selectedModel,
      );
      if (sel) {
        await updateAgentModel(activeAgent.agent_id, sel.provider, sel.model);
      }
      setShowModelModal(false);
      await loadAgents();
      if (agentId) await selectAgent(agentId);
    } catch (e) {
      console.error('Model update failed', e);
    } finally {
      setSavingModel(false);
    }
  };

  const sectionContent: Record<Section, ReactElement> = {
    chat: <AssistantChat />,
    plan: <PlanPanel />,
    ontology: <Box h="100%" overflowY="auto"><OntologyPanel /></Box>,
    resources: <ResourcesPanel />,
    batch: <BatchMonitor />,
    workflow: <WorkflowPanel />,
    wiki: activeAgent ? <WikiPanel agentId={activeAgent.agent_id} /> : (
      <Flex h="100%" align="center" justify="center">
        <Text color="text.tertiary" fontSize="body">Wiki icin bir agent secin</Text>
      </Flex>
    ),
  };

  return (
    <Flex direction="column" h="100%">
      <Box
        px={4}
        py={2}
        borderBottom="1px"
        borderColor="border.subtle"
        bg="surface.primary"
        flexShrink={0}
      >
        <Flex alignItems="center" gap={1.5} fontSize="caption" color="text.tertiary">
          <Link to="/dashboard" className="hover:text-gray-900 transition-colors">
            Dashboard
          </Link>
          <span>/</span>
          <Text color="text.primary" fontWeight="500" fontSize="caption">
            {activeAgent?.name || 'Self-Evolving Agent'}
          </Text>
          {activeAgent && (
            <>
              <Box flex={1} />
              <Tooltip label="Model degistir" fontSize="xs" hasArrow>
                <Badge
                  bg="blue.50"
                  color="blue.700"
                  fontSize="micro"
                  px={2.5}
                  py={0.5}
                  borderRadius="full"
                  fontWeight="500"
                  cursor="pointer"
                  _hover={{ bg: 'blue.100' }}
                  onClick={openModelModal}
                  display="flex"
                  alignItems="center"
                  gap={1}
                >
                  <Cpu size={12} />
                  {currentModelLabel}
                </Badge>
              </Tooltip>
              <Badge
                bg={mode === 'plan' ? 'orange.50' : 'green.50'}
                color={mode === 'plan' ? 'orange.700' : 'green.700'}
                fontSize="micro"
                px={2.5}
                py={0.5}
                borderRadius="full"
                fontWeight="500"
              >
                {mode === 'plan' ? 'Plan Modu' : 'Agent Modu'}
              </Badge>
            </>
          )}
        </Flex>
      </Box>

      <NotificationBar />

      <Flex flex={1} overflow="hidden">
        <EvolvingAgentSidebar />
        <NavSidebar activeSection={activeSection} onSelect={setActiveSection} />
        <Box flex={1} overflow="hidden" h="100%" minH={0}>
          {sectionContent[activeSection]}
        </Box>
      </Flex>

      {/* Model Change Modal */}
      <AnimatePresence>
        {showModelModal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{
              position: 'fixed', inset: 0, zIndex: 50,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'rgba(0,0,0,0.4)', backdropFilter: 'blur(4px)',
            }}
            onClick={() => !savingModel && setShowModelModal(false)}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 10 }}
              transition={{ duration: 0.2 }}
              style={{
                background: 'white', borderRadius: '16px', boxShadow: '0 25px 50px rgba(0,0,0,.25)',
                width: '100%', maxWidth: '420px', margin: '0 16px', overflow: 'hidden',
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <div style={{ padding: '20px 24px', borderBottom: '1px solid #f1f5f9' }}>
                <h2 style={{ fontSize: '16px', fontWeight: 600, color: '#0f172a' }}>
                  Model Degistir
                </h2>
                <p style={{ fontSize: '13px', color: '#64748b', marginTop: '2px' }}>
                  {activeAgent?.name} icin LLM modelini secin
                </p>
              </div>

              <div style={{ padding: '20px 24px' }}>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, color: '#334155', marginBottom: '6px' }}>
                  LLM Model
                </label>
                <select
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  style={{
                    width: '100%', padding: '10px 14px', border: '1px solid #e2e8f0',
                    borderRadius: '12px', fontSize: '14px', background: 'white',
                    outline: 'none',
                  }}
                >
                  <option value="">Sistem Varsayilani</option>
                  {AVAILABLE_MODELS.map((m) => (
                    <option key={`${m.provider}/${m.model}`} value={`${m.provider}/${m.model}`}>
                      {m.label} ({m.provider})
                    </option>
                  ))}
                </select>
                <p style={{ fontSize: '12px', color: '#94a3b8', marginTop: '6px' }}>
                  Degisiklik bir sonraki sohbetten itibaren gecerli olur
                </p>
              </div>

              <div style={{
                padding: '12px 24px', background: '#f8fafc', borderTop: '1px solid #f1f5f9',
                display: 'flex', justifyContent: 'flex-end', gap: '10px',
              }}>
                <button
                  onClick={() => setShowModelModal(false)}
                  disabled={savingModel}
                  style={{
                    padding: '8px 16px', fontSize: '13px', fontWeight: 500, color: '#475569',
                    borderRadius: '12px', background: 'transparent', border: 'none', cursor: 'pointer',
                  }}
                >
                  Iptal
                </button>
                <button
                  onClick={handleSaveModel}
                  disabled={!selectedModel || savingModel}
                  style={{
                    padding: '8px 20px', fontSize: '13px', fontWeight: 500, color: 'white',
                    borderRadius: '12px', background: !selectedModel || savingModel ? '#93c5fd' : '#2563eb',
                    border: 'none', cursor: !selectedModel || savingModel ? 'not-allowed' : 'pointer',
                    display: 'flex', alignItems: 'center', gap: '6px',
                  }}
                >
                  {savingModel && (
                    <span style={{
                      display: 'inline-block', width: '14px', height: '14px',
                      border: '2px solid rgba(255,255,255,.3)', borderTopColor: 'white',
                      borderRadius: '50%', animation: 'spin 0.6s linear infinite',
                    }} />
                  )}
                  Kaydet
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
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
