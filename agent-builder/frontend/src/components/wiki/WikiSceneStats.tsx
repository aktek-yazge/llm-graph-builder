import { useEffect, useState, useCallback } from 'react';
import {
  Box, VStack, HStack, Text, Button, Spinner, Checkbox, useToast, Tooltip,
} from '@chakra-ui/react';
import { RefreshCw, Check, Eye } from 'lucide-react';
import {
  getSceneStats, getScenePublished, publishScene,
  type SceneStats, type ScenePublished,
} from '../../services/evolvingApi';
import {
  CATEGORY_LABELS, CATEGORY_ORDER, CATEGORY_COLORS,
} from './wikiUtils';
import type { WikiCategory } from '../../services/evolvingApi';

interface Props {
  agentId: string;
  onPublished: () => void;
  onPreview: (categories: string[]) => void;
}

const MAX_PROMPT_TOKENS = 12000;

export default function WikiSceneStats({ agentId, onPublished, onPreview }: Props) {
  const toast = useToast();
  const [stats, setStats] = useState<SceneStats | null>(null);
  const [published, setPublished] = useState<ScenePublished | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [publishing, setPublishing] = useState(false);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const categories = Array.from(selected);
      const [statsRes, pubRes] = await Promise.all([
        getSceneStats(agentId, categories.length ? categories : undefined),
        getScenePublished(agentId),
      ]);
      setStats(statsRes.data);
      setPublished(pubRes.data);
    } finally {
      setLoading(false);
    }
  }, [agentId, selected]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const toggle = (cat: string) => {
    setSelected((prev) => {
      if (prev.size === 0) {
        const next = new Set<string>(CATEGORY_ORDER as unknown as string[]);
        next.delete(cat);
        return next;
      }
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      const all = new Set<string>(CATEGORY_ORDER as unknown as string[]);
      let fullMatch = next.size === all.size;
      if (fullMatch) {
        for (const c of all) {
          if (!next.has(c)) { fullMatch = false; break; }
        }
      }
      return fullMatch ? new Set() : next;
    });
  };

  const handlePublish = async () => {
    setPublishing(true);
    try {
      const res = await publishScene(agentId, Array.from(selected));
      toast({
        title: 'Sahne yayinlandi',
        description: `Skill: ${res.data.skill_id} (v${res.data.version})`,
        status: 'success',
        duration: 3500,
        isClosable: true,
      });
      await refresh();
      onPublished();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Bilinmeyen hata';
      toast({ title: 'Yayinlama basarisiz', description: msg, status: 'error' });
    } finally {
      setPublishing(false);
    }
  };

  const tokenPct = stats ? Math.min(100, (stats.token_estimate / MAX_PROMPT_TOKENS) * 100) : 0;
  const tokenColor = tokenPct > 85 ? '#ff6b6b' : tokenPct > 60 ? '#fd7e14' : '#51cf66';
  const publishedMeta = published?.metadata;
  const hasPublished = Boolean(published?.published);

  return (
    <VStack align="stretch" spacing={4}>
      {/* Header */}
      <HStack justify="space-between">
        <Text fontSize="12px" fontWeight="600" color="#868e96" textTransform="uppercase" letterSpacing="0.04em">
          Sahne
        </Text>
        <Button
          size="xs"
          variant="ghost"
          onClick={refresh}
          isLoading={loading}
          p={1}
        >
          <RefreshCw size={12} strokeWidth={1.5} />
        </Button>
      </HStack>

      {loading && !stats ? (
        <HStack><Spinner size="xs" color="#868e96" /><Text fontSize="12px" color="#868e96">Yukleniyor...</Text></HStack>
      ) : stats ? (
        <>
          {/* Token estimate card */}
          <Box p={3} bg="#f8f9fa" borderRadius="8px">
            <HStack justify="space-between" mb={2}>
              <Text fontSize="12px" color="#868e96">Token tahmini</Text>
              <Text fontSize="12px" fontWeight="600" color="#495057">
                ~{stats.token_estimate.toLocaleString()} / {MAX_PROMPT_TOKENS.toLocaleString()}
              </Text>
            </HStack>
            <Box h="3px" bg="#e9ecef" borderRadius="full" overflow="hidden">
              <Box h="100%" w={`${tokenPct}%`} bg={tokenColor} borderRadius="full" transition="width 0.3s" />
            </Box>
            <Text fontSize="11px" color="#adb5bd" mt={1.5}>
              {stats.char_count.toLocaleString()} karakter, ~4 karakter = 1 token
            </Text>
          </Box>

          {/* Category selection */}
          <Box>
            <Text fontSize="12px" fontWeight="600" color="#495057" mb={2}>
              Dahil edilecek kategoriler
            </Text>
            <VStack align="stretch" spacing={1.5}>
              {CATEGORY_ORDER.map((cat) => {
                const count = stats.page_count_by_category[cat] || 0;
                const enabled = count > 0;
                const allIncluded = selected.size === 0;
                const isChecked = allIncluded || selected.has(cat);
                const catColors = CATEGORY_COLORS[cat as WikiCategory];
                return (
                  <HStack key={cat} spacing={2}>
                    <Checkbox
                      size="sm"
                      isChecked={isChecked}
                      isDisabled={!enabled}
                      onChange={() => toggle(cat)}
                      colorScheme="gray"
                    />
                    <Box w="6px" h="6px" borderRadius="full" bg={catColors.dot} flexShrink={0} />
                    <Text fontSize="13px" flex={1} color={enabled ? '#495057' : '#adb5bd'}>
                      {CATEGORY_LABELS[cat as WikiCategory]}
                    </Text>
                    <Text fontSize="11px" color="#adb5bd" fontWeight="500">{count}</Text>
                  </HStack>
                );
              })}
            </VStack>
          </Box>

          {/* Preview */}
          <Button
            size="xs"
            variant="ghost"
            leftIcon={<Eye size={12} strokeWidth={1.5} />}
            onClick={() => onPreview(Array.from(selected))}
            color="#868e96"
            fontWeight="500"
          >
            Prompt'u goster
          </Button>

          {/* Publish status */}
          <Box p={3} bg="#f8f9fa" borderRadius="8px">
            <Text fontSize="12px" fontWeight="600" color="#495057" mb={2}>Yayin durumu</Text>
            {hasPublished ? (
              <VStack align="stretch" spacing={1}>
                <HStack>
                  <Box
                    px={2}
                    py={0.5}
                    bg="#eefbf0"
                    borderRadius="full"
                    display="inline-flex"
                    alignItems="center"
                    gap={1}
                  >
                    <Check size={10} color="#2b7a3c" strokeWidth={2} />
                    <Text fontSize="11px" fontWeight="600" color="#2b7a3c">Yayinda</Text>
                  </Box>
                  <Text fontSize="11px" color="#adb5bd">v{published?.version ?? 1}</Text>
                </HStack>
                <Text fontSize="11px" color="#868e96">
                  Sayfa: {publishedMeta?.page_count ?? 0} &middot; Bag: {publishedMeta?.token_estimate?.toLocaleString() ?? 0}
                </Text>
              </VStack>
            ) : (
              <Text fontSize="12px" color="#adb5bd">Henuz yayinlanmis sahne yok.</Text>
            )}
          </Box>

          {/* Publish button */}
          <Tooltip
            label={
              stats.ontology_empty
                ? 'Ontoloji bos; once entity/relationship ekleyin'
                : 'Wiki + ontolojiyi dondurup skill olarak kaydeder'
            }
          >
            <Button
              w="100%"
              size="sm"
              onClick={handlePublish}
              isLoading={publishing}
              isDisabled={stats.ontology_empty}
            >
              {hasPublished ? 'Yeniden yayinla' : 'Sahneyi yayinla'}
            </Button>
          </Tooltip>
        </>
      ) : null}
    </VStack>
  );
}
