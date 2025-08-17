import {
  Box,
  Typography,
  Flex,
  Tabs,
  Code,
  useCopyToClipboard,
  Banner,
  useMediaQuery,
  Button,
  TextArea,
  IconButton,
} from '@neo4j-ndl/react';
import { DocumentDuplicateIconOutline, ClipboardDocumentCheckIconOutline } from '@neo4j-ndl/react/icons';
import '../../styling/info.css';
import Neo4jRetrievalLogo from '../../assets/images/Neo4jRetrievalLogo.png';
import { ExtendedNode, chatInfoMessage } from '../../types';
import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import GraphViewButton from '../Graph/GraphViewButton';
import { chunkEntitiesAPI } from '../../services/ChunkEntitiesInfo';
import { tokens } from '@neo4j-ndl/base';
import ChunkInfo from './ChunkInfo';
import EntitiesInfo from './EntitiesInfo';
import SourcesInfo from './SourcesInfo';
import CommunitiesInfo from './CommunitiesInfo';
import {
  chatModeLables,
  chatModeReadableLables,
  mergeNestedObjects,
  supportedLLmsForRagas,
} from '../../utils/Constants';
import { Relationship } from '@neo4j-nvl/base';
import { getChatMetrics } from '../../services/GetRagasMetric';
import MetricsTab from './MetricsTab';
import { Stack } from '@mui/material';
import { capitalizeWithUnderscore, getNodes } from '../../utils/Utils';
import MultiModeMetrics from './MultiModeMetrics';
import getAdditionalMetrics from '../../services/AdditionalMetrics';
import { withVisibility } from '../../HOC/WithVisibility';
import MetricsCheckbox from './MetricsCheckbox';

const ChatInfoModal: React.FC<chatInfoMessage> = ({
  sources,
  model,
  total_tokens,
  agent_input_tokens,
  agent_output_tokens,
  agent_total_tokens,
  agent_chunk_details,
  agent_entity_details,
  agent_discovered_entities,
  agent_discovered_chunks,
  agent_iterations,
  response_time,
  nodeDetails,
  mode,
  cypher_query,
  graphonly_entities,
  error,
  entities_ids,
  metricanswer,
  metriccontexts,
  metricquestion,
  metricmodel,
  nodes,
  chunks,
  infoEntities,
  communities,
  metricDetails,
  relationships,
  infoLoading,
  metricsLoading,
  activeChatmodes,
  metricError,
  multiModelMetrics,
  saveNodes,
  saveChunks,
  saveChatRelationships,
  saveCommunities,
  saveInfoEntitites,
  saveMetrics,
  toggleInfoLoading,
  toggleMetricsLoading,
  saveMultimodemetrics,
}) => {
  const { breakpoints } = tokens;
  const isTablet = useMediaQuery(`(min-width:${breakpoints.xs}) and (max-width: ${breakpoints.lg})`);
  const [activeTab, setActiveTab] = useState<number>(
    error?.length
      ? 10
      : mode === chatModeLables['global search+vector+fulltext']
        ? 7
        : mode === chatModeLables.graph
          ? 4
          : 3
  );
  const [, copy] = useCopyToClipboard();
  const [copiedText, setcopiedText] = useState<boolean>(false);
  const [showMetricsTable, setShowMetricsTable] = useState<boolean>(Boolean(metricDetails));
  const [showMultiModeMetrics, setShowMultiModeMetrics] = useState<boolean>(Boolean(multiModelMetrics.length));
  const [multiModeError, setMultiModeError] = useState<string>('');
  const [enableReference, toggleReferenceVisibility] = useReducer((state: boolean) => !state, false);
  const textAreaRef = useRef<HTMLTextAreaElement>(null);
  const [isAdditionalMetricsEnabled, setIsAdditionalMetricsEnabled] = useState<boolean | null>(
    multiModelMetrics.length > 0 && Object.keys(multiModelMetrics[0]).length > 4
      ? true
      : multiModelMetrics.length > 0 && Object.keys(multiModelMetrics[0]).length <= 4
        ? false
        : null
  );
  const [isAdditionalMetricsWithSingleMode, setIsAdditionalMetricsWithSingleMode] = useState<boolean | null>(
    metricDetails != undefined && Object.keys(metricDetails).length > 3
      ? true
      : metricDetails != undefined && Object.keys(metricDetails).length <= 3
        ? false
        : null
  );
  const actions: React.ComponentProps<typeof IconButton<'button'>>[] = useMemo(
    () => [
      {
        title: 'copy',
        ariaLabel: 'copy',
        children: (
          <>
            {copiedText ? (
              <ClipboardDocumentCheckIconOutline className='n-size-token-7' />
            ) : (
              <DocumentDuplicateIconOutline className='text-palette-neutral-text-icon n-size-token-7' />
            )}
          </>
        ),
        onClick: () => {
          void copy(cypher_query as string);
          setcopiedText(true);
        },
      },
    ],
    [copiedText, cypher_query]
  );

  useEffect(() => {
    const abortcontroller = new AbortController();
    if (
      (mode != chatModeLables.graph || error?.trim() !== '') &&
      (!nodes.length || !infoEntities.length || !chunks.length)
    ) {
      (async () => {
        toggleInfoLoading();
        try {
          const response = await chunkEntitiesAPI(nodeDetails, entities_ids, mode, abortcontroller.signal);
          if (response.data.status === 'Failure') {
            throw new Error(response.data.error);
          }
          const nodesData = response?.data?.data?.nodes
            .map((f: Node) => f)
            .filter((node: ExtendedNode) => node.labels.length === 1);
          const nodeIds = new Set(nodesData.map((node: any) => node.element_id));
          const relationshipsData = response?.data?.data?.relationships
            .map((f: Relationship) => f)
            .filter((rel: any) => nodeIds.has(rel.end_node_element_id) && nodeIds.has(rel.start_node_element_id));
          const communitiesData = response?.data?.data?.community_data;
          const chunksData = response?.data?.data?.chunk_data;
          saveInfoEntitites(getNodes(nodesData, mode));
          saveNodes(getNodes(nodesData, mode));
          saveChatRelationships(relationshipsData ?? []);
          saveCommunities(
            (communitiesData ?? [])
              .map((community: { element_id: string }) => {
                const communityScore = nodeDetails?.communitydetails?.find(
                  (c: { id: string }) => c.id === community.element_id
                );
                return {
                  ...community,
                  score: communityScore?.score ?? 1,
                };
              })
              .sort((a: any, b: any) => b.score - a.score)
          );
          saveChunks(
            chunksData
              .map((chunk: any) => {
                const chunkScore = nodeDetails?.chunkdetails?.find((c: any) => c.id === chunk.id);
                return {
                  ...chunk,
                  score: chunkScore?.score,
                };
              })
              .sort((a: any, b: any) => b.score - a.score)
          );
          toggleInfoLoading();
        } catch (error) {
          console.error('Error fetching information:', error);
          toggleInfoLoading();
        }
      })();
    }
    () => {
      setcopiedText(false);
      if (metricsLoading) {
        toggleMetricsLoading();
      }
      abortcontroller.abort();
    };
  }, [nodeDetails, mode, error, metricsLoading]);

  const onChangeTabs = (tabId: number) => {
    setActiveTab(tabId);
  };
  const loadMetrics = async () => {
    // @ts-ignore
    const referenceText = textAreaRef?.current?.value ?? '';
    const metricsPromise = [];
    if (activeChatmodes != undefined && Object.keys(activeChatmodes).length <= 1) {
      setShowMetricsTable(true);
      const [defaultMode] = Object.keys(activeChatmodes);
      try {
        toggleMetricsLoading();
        metricsPromise.push(
          getChatMetrics(metricquestion, [metriccontexts], [metricanswer], metricmodel, [defaultMode])
        );
        if (referenceText.trim() != '') {
          metricsPromise.push(
            getAdditionalMetrics(metricquestion, [metriccontexts], [metricanswer], referenceText, metricmodel, [
              defaultMode,
            ])
          );
          toggleReferenceVisibility();
        }

        const metricsResponse = await Promise.allSettled(metricsPromise);
        const successresponse = [];
        for (let index = 0; index < metricsResponse.length; index++) {
          const metricPromise = metricsResponse[index];
          if (metricPromise.status === 'fulfilled' && metricPromise.value.data.status === 'Success') {
            successresponse.push(metricPromise.value.data.data);
          }
        }
        setIsAdditionalMetricsWithSingleMode(successresponse.length === 2);
        toggleMetricsLoading();
        const mergedState = successresponse.reduce((acc, cur) => {
          if (acc[defaultMode]) {
            acc[defaultMode] = { ...acc[defaultMode], ...cur[defaultMode] };
          } else {
            acc[defaultMode] = cur[defaultMode];
          }
          return acc;
        }, {});
        saveMetrics(mergedState[defaultMode]);
      } catch (error) {
        if (error instanceof Error) {
          setShowMetricsTable(false);
          toggleMetricsLoading();
          console.log('Error in getting chat metrics', error);
          saveMetrics({ faithfulness: 0, answer_relevancy: 0, error: error.message });
        }
      }
    } else if (activeChatmodes != undefined) {
      setShowMultiModeMetrics(true);
      toggleMetricsLoading();
      const values = Object.values(activeChatmodes);
      const keys = Object.keys(activeChatmodes);
      const contextarray = values.map((r) => {
        return r.metric_contexts;
      });
      const answerarray = values.map((r) => {
        return r.metric_answer;
      });
      const modesarray = keys.map((mode) => {
        return mode;
      });
      try {
        metricsPromise.push(
          getChatMetrics(metricquestion, contextarray as string[], answerarray as string[], metricmodel, modesarray)
        );
        if (referenceText.trim() != '') {
          metricsPromise.push(
            getAdditionalMetrics(
              metricquestion,
              contextarray as string[],
              answerarray as string[],
              referenceText,
              metricmodel,
              modesarray
            )
          );
          toggleReferenceVisibility();
        }
        const metricsResponse = await Promise.allSettled(metricsPromise);
        toggleMetricsLoading();
        const successResponse = [];
        for (let index = 0; index < metricsResponse.length; index++) {
          const metricPromise = metricsResponse[index];
          if (metricPromise.status === 'fulfilled' && metricPromise.value.data.status === 'Success') {
            successResponse.push(metricPromise.value.data.data);
          }
        }
        setIsAdditionalMetricsEnabled(successResponse.length === 2);
        const metricsdata = Object.entries(mergeNestedObjects(successResponse)).map(([mode, scores]) => {
          return { mode, ...scores };
        });
        saveMultimodemetrics(metricsdata);
      } catch (error) {
        setShowMultiModeMetrics(false);
        toggleMetricsLoading();
        console.log('Error in getting chat metrics', error);
        if (error instanceof Error) {
          setMultiModeError(error.message);
        }
      }
    }
  };
  const MetricsCheckBoxWithCheck = withVisibility(MetricsCheckbox);
  const TextareaWithCheck = withVisibility(() => (
    <TextArea ref={textAreaRef} isFluid={true} size='large' isOptional={true} label='Referans Cevap'></TextArea>
  ));
  const isMultiModes = useMemo(
    () => activeChatmodes != null && Object.keys(activeChatmodes).length > 1,
    [activeChatmodes]
  );
  const isSingleMode = useMemo(
    () => activeChatmodes != null && Object.keys(activeChatmodes).length <= 1,
    [activeChatmodes]
  );
  return (
    <div className='n-bg-palette-neutral-bg-weak p-4'>
      <div className='flex! flex-row pb-6 items-center mb-2'>
        <img
          src={Neo4jRetrievalLogo}
          style={{ width: isTablet ? 80 : 95, height: isTablet ? 80 : 95, marginRight: 10 }}
          loading='lazy'
          alt='Retrieval-logo'
        />
        <div className='flex! flex-col'>
          <Typography variant='h2'>Retriever Bilgileri</Typography>
          <Typography variant='body-medium' className='mb-2'>
            Bu yanıtı oluşturmak için süreç <span className='font-bold'>{response_time} saniye</span> sürdü.
            <br />
            <span className='font-bold'>RAG Pipeline:</span> <span className='font-bold'>{total_tokens}</span> token
            kullanarak model <span className='font-bold'>{model}</span> ile{' '}
            <span className='font-bold'>
              {chatModeReadableLables[mode] !== 'vector'
                ? chatModeReadableLables[mode].replace(/\+/g, ' & ')
                : chatModeReadableLables[mode]}
            </span>{' '}
            modunda gerçekleştirildi.
            {agent_total_tokens && agent_total_tokens > 0 && (
              <>
                <br />
                <span className='font-bold'>IntelligentAgent:</span>{' '}
                <span className='font-bold'>{agent_input_tokens}</span> input +{' '}
                <span className='font-bold'>{agent_output_tokens}</span> output ={' '}
                <span className='font-bold'>{agent_total_tokens}</span> token kullandı.
                {((agent_discovered_entities && agent_discovered_entities > 0) ||
                  (agent_discovered_chunks && agent_discovered_chunks > 0)) && (
                  <>
                    <br />
                    <span className='font-bold'>Bulunan:</span>{' '}
                    {agent_discovered_entities && agent_discovered_entities > 0 && (
                      <span>
                        <span className='font-bold'>{agent_discovered_entities}</span> entity
                      </span>
                    )}
                    {agent_discovered_entities &&
                      agent_discovered_entities > 0 &&
                      agent_discovered_chunks &&
                      agent_discovered_chunks > 0 &&
                      ', '}
                    {agent_discovered_chunks && agent_discovered_chunks > 0 && (
                      <span>
                        <span className='font-bold'>{agent_discovered_chunks}</span> chunk
                      </span>
                    )}
                    {agent_iterations && agent_iterations > 0 && <span> ({agent_iterations} iterasyon)</span>}
                  </>
                )}
              </>
            )}
          </Typography>
        </div>
      </div>
      {error?.length > 0 ? (
        <Banner type='danger' usage='inline'>
          {error}
        </Banner>
      ) : (
        <Tabs size='large' fill='underline' onChange={onChangeTabs} value={activeTab}>
          {mode === chatModeLables['global search+vector+fulltext'] ? (
            <Tabs.Tab tabId={7}>Communities</Tabs.Tab>
          ) : (
            <>
              {mode != chatModeLables.graph ? <Tabs.Tab tabId={3}>Kullanılan Kaynaklar</Tabs.Tab> : <></>}
              {mode != chatModeLables.graph ? <Tabs.Tab tabId={5}>Chunk'lar</Tabs.Tab> : <></>}
              {agent_chunk_details && agent_chunk_details.length > 0 && <Tabs.Tab tabId={9}>Agent Chunk'ları</Tabs.Tab>}
              {mode === chatModeLables['graph+vector'] ||
              mode === chatModeLables.graph ||
              mode === chatModeLables['graph+vector+fulltext'] ||
              mode === chatModeLables['entity search+vector'] ? (
                <Tabs.Tab tabId={4}>Kullanılan En İyi Entity'ler</Tabs.Tab>
              ) : (
                <></>
              )}
              {mode === chatModeLables.graph && cypher_query?.trim()?.length ? (
                <Tabs.Tab tabId={6}>Oluşturulan Cypher Sorgusu</Tabs.Tab>
              ) : (
                <></>
              )}
              {mode === chatModeLables['entity search+vector'] && communities.length ? (
                <Tabs.Tab tabId={7}>Community'ler</Tabs.Tab>
              ) : (
                <></>
              )}
              <Tabs.Tab tabId={8}>Değerlendirme Metrikleri</Tabs.Tab>
            </>
          )}
        </Tabs>
      )}
      <Flex className='p-4'>
        <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={3}>
          <SourcesInfo loading={infoLoading} sources={sources} mode={mode} chunks={chunks} />
        </Tabs.TabPanel>
        <Tabs.TabPanel tabId={8} value={activeTab}>
          <Stack spacing={2}>
            <Stack spacing={2}>
              {!supportedLLmsForRagas.includes(metricmodel) && (
                <Banner
                  type='warning'
                  title='LLM Modeli Desteklenmiyor, Lütfen Farklı Model Seçin'
                  description={
                    <Typography variant='body-medium'>
                      Şu anda ragas değerlendirmesi şu modellerde çalışmaktadır:{' '}
                      {supportedLLmsForRagas.map((s, idx) => (
                        <span className='font-bold' key={s}>
                          {capitalizeWithUnderscore(s) + (idx != supportedLLmsForRagas.length - 1 ? ',' : '')}
                        </span>
                      ))}
                      .
                    </Typography>
                  }
                  usage='inline'
                ></Banner>
              )}
              <Box>
                <Typography variant='body-large'>
                  Sohbet yanıtlarımızın kalitesini değerlendirmek için birkaç temel metrik kullanırız. Bu etkileşim için
                  <span className='font-bold'> ragas framework</span> kullanarak detaylı puanları görüntülemek için
                  aşağıdaki butona tıklayın. Bu puanlar chatbot'larımızın doğruluğunu ve yararlılığını sürekli
                  iyileştirmemize yardımcı olur. Bu işlem genellikle yaklaşık{' '}
                  <span className='font-bold'>20 saniye</span> sürer. Kısa süre içinde detaylı puanları göreceksiniz.
                </Typography>
              </Box>
            </Stack>
            {showMultiModeMetrics && isMultiModes && (
              <MultiModeMetrics
                error={multiModeError}
                metricsLoading={metricsLoading}
                data={multiModelMetrics}
                isWithAdditionalMetrics={isAdditionalMetricsEnabled}
              ></MultiModeMetrics>
            )}
            {showMetricsTable && isSingleMode && (
              <MetricsTab metricsLoading={metricsLoading} error={metricError} metricDetails={metricDetails} />
            )}
            <MetricsCheckBoxWithCheck
              enableReference={enableReference}
              toggleReferenceVisibility={toggleReferenceVisibility}
              isVisible={
                isSingleMode &&
                !metricsLoading &&
                (isAdditionalMetricsWithSingleMode === false || isAdditionalMetricsWithSingleMode === null)
              }
            />
            <MetricsCheckBoxWithCheck
              enableReference={enableReference}
              toggleReferenceVisibility={toggleReferenceVisibility}
              isVisible={
                isMultiModes &&
                !metricsLoading &&
                (isAdditionalMetricsEnabled === false || isAdditionalMetricsEnabled === null)
              }
            />
            <TextareaWithCheck
              isVisible={
                enableReference &&
                isSingleMode &&
                (isAdditionalMetricsWithSingleMode === false || isAdditionalMetricsWithSingleMode === null)
              }
            />
            <TextareaWithCheck
              isVisible={
                enableReference &&
                isMultiModes &&
                (isAdditionalMetricsEnabled === false || isAdditionalMetricsEnabled === null)
              }
            />
            {isSingleMode &&
              (isAdditionalMetricsWithSingleMode === false || isAdditionalMetricsWithSingleMode === null) && (
                <Button
                  isDisabled={metricsLoading || !supportedLLmsForRagas.includes(metricmodel)}
                  className='w-max self-center mt-4'
                  onClick={loadMetrics}
                >
                  Detaylı Metrikleri Görüntüle
                </Button>
              )}
            {isMultiModes && (isAdditionalMetricsEnabled === false || isAdditionalMetricsEnabled === null) && (
              <Button
                isDisabled={metricsLoading || !supportedLLmsForRagas.includes(metricmodel)}
                className='w-max self-center mt-4'
                onClick={loadMetrics}
              >
                Tüm Modlar İçin Detaylı Metrikleri Görüntüle
              </Button>
            )}
          </Stack>
        </Tabs.TabPanel>
        <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={4}>
          <EntitiesInfo
            loading={infoLoading}
            mode={mode}
            graphonly_entities={graphonly_entities}
            infoEntities={
              mode === 'agent' && agent_entity_details
                ? agent_entity_details.map((entity) => ({
                    element_id: entity.id,
                    labels: entity.labels,
                    properties: {
                      id: entity.id,
                    },
                  }))
                : infoEntities
            }
          />
        </Tabs.TabPanel>
        <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={5}>
          <ChunkInfo chunks={chunks} loading={infoLoading} mode={mode} />
        </Tabs.TabPanel>
        <Tabs.TabPanel value={activeTab} tabId={6}>
          <Code
            code={cypher_query as string}
            actions={actions}
            headerTitle=''
            theme={'vs'}
            className='min-h-40'
            language='cypher'
          />
        </Tabs.TabPanel>
        {mode === chatModeLables['entity search+vector'] || mode === chatModeLables['global search+vector+fulltext'] ? (
          <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={7}>
            <CommunitiesInfo loading={infoLoading} communities={communities} mode={mode} />
          </Tabs.TabPanel>
        ) : (
          <></>
        )}
        {agent_chunk_details && agent_chunk_details.length > 0 && (
          <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={9}>
            <Typography variant='h5' className='n-mb-token-4'>
              IntelligentAgent Tarafından Bulunan Chunk'lar
            </Typography>
            <div className='max-h-96 overflow-y-auto'>
              {agent_chunk_details.map((chunk, index) => (
                <div key={index} className='border border-gray-200 rounded-lg p-4 mb-3'>
                  <div className='flex justify-between items-start mb-2'>
                    <Typography variant='h6' className='font-semibold'>
                      {chunk.document}
                    </Typography>
                    <div className='text-right text-sm text-gray-600'>
                      <div>Sayfa: {chunk.page}</div>
                      <div>Relevance: {chunk.relevance.toFixed(3)}</div>
                    </div>
                  </div>
                  <Typography variant='body-medium' className='text-gray-700 bg-gray-50 p-2 rounded'>
                    {chunk.preview}
                  </Typography>
                </div>
              ))}
            </div>
          </Tabs.TabPanel>
        )}
      </Flex>
      {activeTab == 4 && nodes?.length && relationships?.length && mode !== chatModeLables.graph ? (
        <div className='button-container flex! mt-2 justify-center'>
          <GraphViewButton
            nodeValues={nodes}
            relationshipValues={relationships}
            label='Cevap Üretimi İçin Kullanılan Graph Entityleri'
            viewType='chatInfoView'
          />
        </div>
      ) : (
        <></>
      )}
    </div>
  );
};
export default ChatInfoModal;
