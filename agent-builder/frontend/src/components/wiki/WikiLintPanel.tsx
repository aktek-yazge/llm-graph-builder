import { useEffect, useState } from 'react';
import {
  Box, VStack, HStack, Text, Button, Badge, Spinner, Divider, List, ListItem, Tag,
} from '@chakra-ui/react';
import { RepeatIcon, WarningIcon } from '@chakra-ui/icons';
import { wikiLint, type WikiLintReport } from '../../services/evolvingApi';

interface Props {
  agentId: string;
  onSelectPage: (path: string) => void;
}

export default function WikiLintPanel({ agentId, onSelectPage }: Props) {
  const [report, setReport] = useState<WikiLintReport | null>(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const res = await wikiLint(agentId);
      setReport(res.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    run();
  }, [agentId]);

  return (
    <VStack align="stretch" spacing={2}>
      <HStack justify="space-between">
        <HStack>
          <WarningIcon color="orange.500" />
          <Text fontSize="sm" fontWeight="semibold">Wiki saglik raporu</Text>
          {report && (
            <Badge colorScheme={report.issues_count === 0 ? 'green' : 'orange'}>
              {report.issues_count} sorun
            </Badge>
          )}
        </HStack>
        <Button size="xs" leftIcon={<RepeatIcon />} onClick={run} isLoading={loading}>
          Yeniden tara
        </Button>
      </HStack>

      {loading && !report ? (
        <HStack><Spinner size="xs" /><Text fontSize="xs" color="gray.500">Taraniyor...</Text></HStack>
      ) : report ? (
        <>
          <HStack spacing={4} fontSize="xs" color="gray.600">
            <Text>Sayfa: {report.total_pages}</Text>
            <Text>Bag: {report.total_links}</Text>
          </HStack>

          <Divider />

          <Box>
            <Text fontSize="xs" fontWeight="semibold" mb={1}>
              Yetim sayfalar ({report.orphan_pages.length})
            </Text>
            {report.orphan_pages.length === 0 ? (
              <Text fontSize="xs" color="gray.500">Yok</Text>
            ) : (
              <List spacing={0.5}>
                {report.orphan_pages.map((p) => (
                  <ListItem key={p}>
                    <Tag
                      size="sm"
                      cursor="pointer"
                      onClick={() => onSelectPage(p)}
                      colorScheme="gray"
                    >
                      {p}
                    </Tag>
                  </ListItem>
                ))}
              </List>
            )}
          </Box>

          <Box>
            <Text fontSize="xs" fontWeight="semibold" mb={1}>
              Kirik baglar ({report.broken_links.length})
            </Text>
            {report.broken_links.length === 0 ? (
              <Text fontSize="xs" color="gray.500">Yok</Text>
            ) : (
              <List spacing={0.5}>
                {report.broken_links.map((b, i) => (
                  <ListItem key={i}>
                    <HStack fontSize="xs">
                      <Tag
                        size="sm"
                        cursor="pointer"
                        onClick={() => onSelectPage(b.source)}
                        colorScheme="indigo"
                      >
                        {b.source}
                      </Tag>
                      <Text color="gray.500">-&gt;</Text>
                      <Tag size="sm" colorScheme="red">{b.target}</Tag>
                    </HStack>
                  </ListItem>
                ))}
              </List>
            )}
          </Box>
        </>
      ) : null}
    </VStack>
  );
}
