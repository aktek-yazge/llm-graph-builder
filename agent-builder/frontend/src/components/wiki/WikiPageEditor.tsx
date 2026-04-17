import { useState, useMemo } from 'react';
import {
  Box, Flex, HStack, Text, Button, Textarea, Input, ButtonGroup,
  Tabs, TabList, TabPanels, Tab, TabPanel,
} from '@chakra-ui/react';
import { CheckIcon, CloseIcon } from '@chakra-ui/icons';
import ReactMarkdown from 'react-markdown';
import { wikilinksToMarkdown } from './wikiUtils';

interface Props {
  initialPath: string;
  initialContent: string;
  isNew: boolean;
  onSave: (path: string, content: string) => Promise<void>;
  onCancel: () => void;
}

export default function WikiPageEditor({
  initialPath, initialContent, isNew, onSave, onCancel,
}: Props) {
  const [path, setPath] = useState(initialPath);
  const [content, setContent] = useState(initialContent);
  const [saving, setSaving] = useState(false);
  const [tabIndex, setTabIndex] = useState(0);

  const canSave = useMemo(
    () => path.trim().length > 0 && content.trim().length > 0 && !saving,
    [path, content, saving]
  );

  const handleSave = async () => {
    if (!canSave) return;
    try {
      setSaving(true);
      await onSave(path.trim(), content);
    } finally {
      setSaving(false);
    }
  };

  const preview = useMemo(() => wikilinksToMarkdown(content), [content]);

  return (
    <Flex direction="column" h="100%" bg="white">
      <Flex
        px={6}
        py={3}
        borderBottom="1px solid"
        borderColor="gray.200"
        align="center"
        gap={3}
        flexShrink={0}
      >
        <Box flex={1} minW={0}>
          <Text fontSize="xs" color="gray.500">
            {isNew ? 'Yeni sayfa' : 'Sayfayi duzenle'}
          </Text>
          <Input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="entities/sirket"
            size="sm"
            isReadOnly={!isNew}
            mt={1}
            fontFamily="mono"
          />
        </Box>
        <ButtonGroup size="sm">
          <Button
            leftIcon={<CheckIcon />}
            colorScheme="green"
            isLoading={saving}
            onClick={handleSave}
            isDisabled={!canSave}
          >
            Kaydet
          </Button>
          <Button leftIcon={<CloseIcon />} variant="outline" onClick={onCancel}>
            Iptal
          </Button>
        </ButtonGroup>
      </Flex>

      <Tabs
        index={tabIndex}
        onChange={setTabIndex}
        variant="line"
        size="sm"
        display="flex"
        flexDirection="column"
        flex={1}
        overflow="hidden"
      >
        <TabList px={6} flexShrink={0}>
          <Tab>Markdown</Tab>
          <Tab>Onizleme</Tab>
          <Tab>Yan yana</Tab>
        </TabList>

        <TabPanels flex={1} overflow="hidden">
          <TabPanel p={0} h="100%">
            <Textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="# Sayfa basligi..."
              fontFamily="mono"
              fontSize="sm"
              h="100%"
              borderRadius={0}
              border="none"
              _focus={{ boxShadow: 'none', border: 'none' }}
              resize="none"
              p={6}
            />
          </TabPanel>
          <TabPanel p={0} h="100%">
            <Box h="100%" overflowY="auto" p={6}>
              <ReactMarkdown>{preview}</ReactMarkdown>
            </Box>
          </TabPanel>
          <TabPanel p={0} h="100%">
            <HStack h="100%" spacing={0} align="stretch">
              <Box flex={1} h="100%" borderRight="1px solid" borderColor="gray.200">
                <Textarea
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  fontFamily="mono"
                  fontSize="sm"
                  h="100%"
                  borderRadius={0}
                  border="none"
                  _focus={{ boxShadow: 'none', border: 'none' }}
                  resize="none"
                  p={4}
                />
              </Box>
              <Box flex={1} h="100%" overflowY="auto" p={4} bg="gray.50">
                <ReactMarkdown>{preview}</ReactMarkdown>
              </Box>
            </HStack>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </Flex>
  );
}
