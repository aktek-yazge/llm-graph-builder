import { useEffect, useState, useCallback } from 'react';
import {
  Box, VStack, HStack, Text, Button, Badge, Spinner, Divider,
  Progress, Checkbox, useToast, Tooltip, Tag,
} from '@chakra-ui/react';
import { StarIcon, RepeatIcon, CheckIcon } from '@chakra-ui/icons';
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
  const tokenColor = tokenPct > 85 ? 'red' : tokenPct > 60 ? 'orange' : 'green';

  const publishedMeta = published?.metadata;
  const hasPublished = Boolean(published?.published);

  return (
    <VStack align="stretch" spacing={3}>
      <HStack justify="space-between">
        <HStack>
          <StarIcon color="purple.500" />
          <Text fontSize="sm" fontWeight="semibold">Sahne</Text>
        </HStack>
        <Button size="xs" leftIcon={<RepeatIcon />} onClick={refresh} isLoading={loading}>
          Yenile
        </Button>
      </HStack>

      {loading && !stats ? (
        <HStack><Spinner size="xs" /><Text fontSize="xs">Yukleniyor...</Text></HStack>
      ) : stats ? (
        <>
          <Box>
            <HStack justify="space-between" mb={1}>
              <Text fontSize="xs" color="gray.600">Token tahmini</Text>
              <Text fontSize="xs" fontWeight="semibold">
                ~{stats.token_estimate.toLocaleString()} / {MAX_PROMPT_TOKENS.toLocaleString()}
              </Text>
            </HStack>
            <Progress value={tokenPct} colorScheme={tokenColor} size="xs" borderRadius="full" />
            <Text fontSize="2xs" color="gray.500" mt={1}>
              {stats.char_count.toLocaleString()} karakter, ~4 karakter = 1 token
            </Text>
          </Box>

          <Divider />

          <Box>
            <Text fontSize="xs" fontWeight="semibold" mb={2}>
              Dahil edilecek kategoriler
            </Text>
            <Text fontSize="2xs" color="gray.500" mb={2}>
              Hic secim yoksa tum kategoriler dahil edilir.
            </Text>
            <VStack align="stretch" spacing={1}>
              {CATEGORY_ORDER.map((cat) => {
                const count = stats.page_count_by_category[cat] || 0;
                const enabled = count > 0;
                const allIncluded = selected.size === 0;
                const isChecked = allIncluded || selected.has(cat);
                return (
                  <HStack key={cat} spacing={2}>
                    <Checkbox
                      size="sm"
                      isChecked={isChecked}
                      isDisabled={!enabled}
                      onChange={() => toggle(cat)}
                    />
                    <Box w="8px" h="8px" borderRadius="full" bg={CATEGORY_COLORS[cat as WikiCategory]} />
                    <Text fontSize="xs" flex={1} color={enabled ? 'gray.800' : 'gray.400'}>
                      {CATEGORY_LABELS[cat as WikiCategory]}
                    </Text>
                    <Badge fontSize="2xs">{count}</Badge>
                  </HStack>
                );
              })}
            </VStack>
            <HStack mt={2} spacing={2}>
              <Button
                size="xs"
                variant="outline"
                onClick={() => setSelected(new Set())}
                isDisabled={selected.size === 0}
              >
                Tumu
              </Button>
            </HStack>
          </Box>

          <Divider />

          <HStack justify="space-between">
            <Text fontSize="xs" fontWeight="semibold">Onizleme</Text>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => onPreview(Array.from(selected))}
            >
              Prompt'u goster
            </Button>
          </HStack>

          <Divider />

          <Box>
            <Text fontSize="xs" fontWeight="semibold" mb={1}>Yayin durumu</Text>
            {hasPublished ? (
              <VStack align="stretch" spacing={1} fontSize="xs">
                <HStack>
                  <Tag size="sm" colorScheme="green" borderRadius="full">
                    <CheckIcon boxSize={2.5} mr={1} />Yayinda
                  </Tag>
                  <Text color="gray.500">v{published?.version ?? 1}</Text>
                </HStack>
                <Text color="gray.500">
                  Dondurulma: {publishedMeta?.frozen_at?.slice(0, 19).replace('T', ' ')}
                </Text>
                <Text color="gray.500">
                  {publishedMeta?.page_count ?? 0} sayfa, ~
                  {publishedMeta?.token_estimate?.toLocaleString() ?? 0} token
                </Text>
                {publishedMeta?.categories && publishedMeta.categories.length > 0 && (
                  <HStack wrap="wrap" spacing={1}>
                    {publishedMeta.categories.map((c) => (
                      <Tag key={c} size="sm" colorScheme="purple">{c}</Tag>
                    ))}
                  </HStack>
                )}
              </VStack>
            ) : (
              <Text fontSize="xs" color="gray.500">Henuz yayinlanmis sahne yok.</Text>
            )}
          </Box>

          <Tooltip
            label={
              stats.ontology_empty
                ? 'Ontoloji bos; once entity/relationship ekleyin'
                : hasPublished
                  ? 'Mevcut wiki + ontolojiyi dondurup Celery icin skill olarak kaydeder'
                  : 'Wiki + ontolojiyi dondurup Celery icin skill olarak kaydeder'
            }
          >
            <Button
              colorScheme="purple"
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
