import { useEffect, useState } from 'react';
import {
  Drawer, DrawerOverlay, DrawerContent, DrawerHeader, DrawerBody, DrawerCloseButton,
  List, ListItem, Text, Spinner, HStack, Tag, VStack,
} from '@chakra-ui/react';
import { wikiPageHistory, type WikiHistoryResponse } from '../../services/evolvingApi';
import { formatTimestamp } from './wikiUtils';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  agentId: string;
  path: string | null;
}

export default function WikiHistoryDrawer({ isOpen, onClose, agentId, path }: Props) {
  const [hist, setHist] = useState<WikiHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!isOpen || !path) return;
    const run = async () => {
      setLoading(true);
      try {
        const res = await wikiPageHistory(agentId, path);
        setHist(res.data);
      } finally {
        setLoading(false);
      }
    };
    run();
  }, [isOpen, agentId, path]);

  return (
    <Drawer isOpen={isOpen} placement="right" onClose={onClose} size="sm">
      <DrawerOverlay />
      <DrawerContent>
        <DrawerCloseButton />
        <DrawerHeader>
          Surum gecmisi
          {path && (
            <Text fontSize="xs" color="gray.500" fontWeight="normal" fontFamily="mono">
              {path}
            </Text>
          )}
        </DrawerHeader>
        <DrawerBody>
          {loading ? (
            <HStack><Spinner size="sm" /><Text fontSize="sm">Yukleniyor...</Text></HStack>
          ) : !hist || hist.versions.length === 0 ? (
            <Text fontSize="sm" color="gray.500">Surum kaydi yok.</Text>
          ) : (
            <List spacing={2}>
              {hist.versions.map((v) => (
                <ListItem key={v.version} borderWidth="1px" borderRadius="md" p={2}>
                  <VStack align="start" spacing={1}>
                    <HStack>
                      <Tag size="sm" colorScheme="indigo">v{v.version}</Tag>
                      <Tag size="sm" colorScheme="gray">{v.source || 'unknown'}</Tag>
                    </HStack>
                    <Text fontSize="xs" color="gray.500">
                      {formatTimestamp(v.created_at)}
                    </Text>
                  </VStack>
                </ListItem>
              ))}
            </List>
          )}
        </DrawerBody>
      </DrawerContent>
    </Drawer>
  );
}
