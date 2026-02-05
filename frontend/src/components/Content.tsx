import { useAuth0 } from '@auth0/auth0-react';
import { tokens } from '@neo4j-ndl/base';
import {
  Button,
  Checkbox,
  Flex,
  Menu,
  SpotlightTarget,
  StatusIndicator,
  TextInput,
  Typography,
  useMediaQuery,
  useSpotlightContext,
} from '@neo4j-ndl/react';
import { ChartBarIconOutline, ChevronDownIconOutline, ChevronUpIconOutline, MagnifyingGlassIconOutline, ServerStackIconOutline, XMarkIconOutline } from '@neo4j-ndl/react/icons';
import axios from 'axios';
import React, {
  lazy,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';
import { ThemeWrapperContext } from '../context/ThemeWrapper';
import { useCredentials } from '../context/UserCredentials';
import { useMessageContext } from '../context/UserMessages';
import { useFileContext } from '../context/UsersFiles';
import { useHasSelections } from '../hooks/useHasSelections';
import useServerSideEvent from '../hooks/useSse';
import deleteAPI from '../services/DeleteFiles';
import { getChunkText } from '../services/getChunkText';
import { postProcessing } from '../services/PostProcessing';
import retry from '../services/Retry';
import { triggerStatusUpdateAPI } from '../services/ServerSideStatusUpdateAPI';
import { BannerAlertProps, chunkdata, ContentProps, CustomFile, FileTableHandle, OptionType } from '../types';
import {
  batchSize,
  buttonCaptions,
  chatModeLables,
  chunkOverlap,
  chunksToCombine,
  largeFileSize,
  llms,
  RETRY_OPIONS,
  tokenchunkSize,
  tooltips,
} from '../utils/Constants';
import {
  deleteFileFromQueueAPI,
  extractAPI,
  getBackgroundProcessingStatusAPI,
  resetFileStageAPI,
  startBackgroundProcessingAPI,
  startChunkingAPI,
  startEmbeddingAPI,
  startEndorsementGraphCreationAPI,
} from '../utils/FileAPI';
import { showErrorToast, showNormalToast, showSuccessToast } from '../utils/Toasts';
import { normalizeFileName } from '../utils/utf8';
import { isExpired, isFileReadyToProcess } from '../utils/Utils';
import DropdownComponent from './Dropdown';
import FileTable from './FileTable';
import CreateEmbeddingsModal from './Graph/CreateEmbeddingsModal';
import CreateEntityEmbeddingsModal from './Graph/CreateEntityEmbeddingsModal';
import GraphViewModal from './Graph/GraphViewModal';
import MergeDuplicateEntitiesModal from './Graph/MergeDuplicateEntitiesModal';
import ChunkPopUp from './Popups/ChunkPopUp';
import DeletePopUp from './Popups/DeletePopUp/DeletePopUp';
import GraphEnhancementDialog from './Popups/GraphEnhancementDialog';
import PostProcessingToast from './Popups/GraphEnhancementDialog/PostProcessingCheckList/PostProcessingToast';

import RetryConfirmationDialog from './Popups/RetryConfirmation/Index';
import SystemMonitorModal from './Popups/SystemMonitorModal';
import ProcessingStats from './ProcessingStats';
import ButtonWithToolTip from './UI/ButtonWithToolTip';
import DatabaseStatusIcon from './UI/DatabaseStatusIcon';
import FallBackDialog from './UI/FallBackDialog';

const ConfirmationDialog = lazy(() => import('./Popups/LargeFilePopUp/ConfirmationDialog'));

let afterFirstRender = false;
const Content: React.FC<ContentProps> = ({
  showEnhancementDialog,
  toggleEnhancementDialog,
  setOpenConnection,
  showDisconnectButton,
  connectionStatus,
  combinedPatterns,
  setCombinedPatterns,
  combinedNodes,
  setCombinedNodes,
  combinedRels,
  setCombinedRels,
}) => {
  const { breakpoints } = tokens;
  const isTablet = useMediaQuery(`(min-width:${breakpoints.xs}) and (max-width: ${breakpoints.lg})`);
  const [openGraphView, setOpenGraphView] = useState<boolean>(false);
  const [inspectedName, setInspectedName] = useState<string>('');
  const [documentName, setDocumentName] = useState<string>('');
  const [showConfirmationModal, setShowConfirmationModal] = useState<boolean>(false);
  const [showExpirationModal, setShowExpirationModal] = useState<boolean>(false);
  const [extractLoading, setIsExtractLoading] = useState<boolean>(false);
  const { setUserCredentials, userCredentials, setConnectionStatus, isGdsActive, isReadOnlyUser, isGCSActive, watchProcessingMode } =
    useCredentials();
  const [retryFile, setRetryFile] = useState<string>('');
  const [retryLoading, setRetryLoading] = useState<boolean>(false);
  const [showRetryPopup, toggleRetryPopup] = useReducer((state) => !state, false);
  const [showChunkPopup, toggleChunkPopup] = useReducer((state) => !state, false);
  const [showStatsModal, setShowStatsModal] = useState<boolean>(false);
  const [showSystemMonitorModal, setShowSystemMonitorModal] = useState<boolean>(false);
  const [chunksLoading, toggleChunksLoading] = useReducer((state) => !state, false);
  const [currentPage, setCurrentPage] = useState<number>(0);
  const [totalPageCount, setTotalPageCount] = useState<number | null>(null);
  const [textChunks, setTextChunks] = useState<chunkdata[]>([]);
  const [isGraphBtnMenuOpen, setIsGraphBtnMenuOpen] = useState<boolean>(false);
  const [isChunkingBtnMenuOpen, setIsChunkingBtnMenuOpen] = useState<boolean>(false);
  const [openMergeDuplicateModal, setOpenMergeDuplicateModal] = useState<boolean>(false);
  const [openCreateEmbeddingsModal, setOpenCreateEmbeddingsModal] = useState<boolean>(false);
  const [openCreateEntityEmbeddingsModal, setOpenCreateEntityEmbeddingsModal] = useState<boolean>(false);
  const graphbtnRef = useRef<HTMLDivElement>(null);
  const chunkingbtnRef = useRef<HTMLDivElement>(null);
  const chunksTextAbortController = useRef<AbortController>();
  const { colorMode } = useContext(ThemeWrapperContext);
  const { isAuthenticated } = useAuth0();
  const { setIsOpen } = useSpotlightContext();
  const [v2SelectedFileCount, setV2SelectedFileCount] = useState<number>(0);
  const [v2FilesCategorized, setV2FilesCategorized] = useState<{
    pendingChunking: number;
    readyForGraph: number;
    pendingEndorsement: number;
    completed: number;
  }>({
    pendingChunking: 0,
    readyForGraph: 0,
    pendingEndorsement: 0,
    completed: 0,
  });
  const [alertStateForRetry, setAlertStateForRetry] = useState<BannerAlertProps>({
    showAlert: false,
    alertType: 'neutral',
    alertMessage: '',
  });

  // Queue processing durumu için state - localStorage'dan initialize et
  const [isQueueProcessingStopped, setIsQueueProcessingStopped] = useState<boolean>(() => {
    const saved = localStorage.getItem('isQueueProcessingStopped');
    return saved ? JSON.parse(saved) : false;
  });

  // Dosya adı arama filtresi
  const [nameFilter, setNameFilter] = useState<string>('');

  const { setMessages } = useMessageContext();
  const {
    filesData,
    setFilesData,
    setModel,
    selectedNodes,
    selectedRels,
    selectedTokenChunkSize,
    selectedChunk_overlap,
    selectedChunks_to_combine,
    selectedMaxPages,
    setSelectedMaxPages,
    generateEmbedding,
    setGenerateEmbedding,
    setSelectedNodes,
    setAllPatterns,
    rowSelection,
    setRowSelection,
    setSelectedRels,
    setSelectedTokenChunkSize,
    setSelectedChunk_overlap,
    setSelectedChunks_to_combine,
    entityRelationshipRules,
    postProcessingTasks,
    setPostProcessingTasks,
    queue,
    processedCount,
    setProcessedCount,
    setchatModes,
    model,
    additionalInstructions,
    setAdditionalInstructions,
  } = useFileContext();
  const [viewPoint, setViewPoint] = useState<
    'tableView' | 'showGraphView' | 'chatInfoView' | 'neighborView' | 'showSchemaView'
  >('tableView');
  const [showDeletePopUp, setShowDeletePopUp] = useState<boolean>(false);
  const [deleteLoading, setIsDeleteLoading] = useState<boolean>(false);

  const hasSelections = useHasSelections(selectedNodes, selectedRels);

  const { updateStatusForLargeFiles } = useServerSideEvent(
    (inMinutes, time, fileName) => {
      showNormalToast(`${fileName} will take approx ${time} ${inMinutes ? 'Min' : 'Sec'}`);
      localStorage.setItem('alertShown', JSON.stringify(true));
    },
    (fileName) => {
      showErrorToast(`${fileName} Failed to process`);
    }
  );
  const childRef = useRef<FileTableHandle>(null);

  const incrementPage = async () => {
    setCurrentPage((prev) => prev + 1);
    await getChunks(documentName, currentPage + 1);
  };
  const decrementPage = async () => {
    setCurrentPage((prev) => prev - 1);
    await getChunks(documentName, currentPage - 1);
  };

  useEffect(() => {
    if (afterFirstRender) {
      localStorage.setItem('processedCount', JSON.stringify({ db: userCredentials?.uri, count: processedCount }));
    }
    if (processedCount == batchSize && !isReadOnlyUser && !isQueueProcessingStopped) {
      handleGenerateGraph([], true);
    }
    // Post-processing artık sadece tüm dosyalar bittiğinde addFilesToQueue fonksiyonunda yapılıyor
    // İlk dosya bittiğinde post-processing yapmıyoruz çünkü duplicate çağrı oluyor
  }, [processedCount, userCredentials, queue, isReadOnlyUser, isGdsActive, isQueueProcessingStopped]);

  // Queue processing durumunu localStorage'a kaydet
  useEffect(() => {
    localStorage.setItem('isQueueProcessingStopped', JSON.stringify(isQueueProcessingStopped));
  }, [isQueueProcessingStopped]);

  useEffect(() => {
    if (afterFirstRender) {
      localStorage.setItem('waitingQueue', JSON.stringify({ db: userCredentials?.uri, queue: queue.items }));
    }
    afterFirstRender = true;
  }, [queue.items.length, userCredentials]);
  const isFirstTimeUser = useMemo(() => {
    return localStorage.getItem('neo4j.connection') === null;
  }, []);
  useEffect(() => {
    if (!isAuthenticated && !connectionStatus && isFirstTimeUser) {
      setIsOpen(true);
    }
  }, [connectionStatus, isAuthenticated, isFirstTimeUser]);

  // V2 Selected File Count ve kategorileri güncellemek için useEffect
  // rowSelection state'i doğrudan kullanılarak hesaplama yapılıyor (childRef yerine)
  useEffect(() => {
    // rowSelection'dan seçili ID'leri al (format: {id: true, id2: true, ...})
    const selectedIds = Object.keys(rowSelection).filter(id => rowSelection[id]);
    
    if (selectedIds.length === 0) {
      setV2FilesCategorized({ pendingChunking: 0, readyForGraph: 0, pendingEndorsement: 0, completed: 0 });
      setV2SelectedFileCount(0);
      return;
    }

    // filesData'dan seçili dosyaları al (rowSelection ID'leri ile eşleştir)
    const selectedFiles = filesData.filter(
      (f: CustomFile) => f.fileSource === 'V2 Queue' && selectedIds.includes(f.id)
    );

    const totalCount = selectedFiles.length;

    // Kategorileri hesapla - dosyaların güncel status'lerini kullan
    const pendingChunking = selectedFiles.filter(
      (f: CustomFile) => f.chunking_status === 'ready' || f.status === 'Ready for Chunking'
    ).length;
    
    const extractingImages = selectedFiles.filter(
      (f: CustomFile) => f.chunking_status === 'extracting' || f.status === 'Extracting'
    ).length;

    const readyForGraph = selectedFiles.filter(
      (f: CustomFile) => (f.chunking_status === 'chunked' && f.graph_status === 'pending') || 
                         f.status === 'Ready for Graph' || f.status === 'Chunked'
    ).length;

    const pendingEndorsement = selectedFiles.filter(
      (f: CustomFile) => f.graph_status === 'pending_endorsement' || f.status === 'Pending Endorsement'
    ).length;
    
    const processing = selectedFiles.filter(
      (f: CustomFile) => f.status?.includes('Processing') || f.chunking_status === 'chunking' || 
                         f.graph_status === 'processing'
    ).length;

    setV2SelectedFileCount(totalCount);

    setV2FilesCategorized({ pendingChunking, readyForGraph, pendingEndorsement, completed: 0 });
    
    // Debug log
    console.log('📊 V2 Files Categorized:', { 
      total: totalCount, 
      pendingChunking, 
      readyForGraph, 
      processing,
      pendingEndorsement 
    });
  }, [filesData, rowSelection]);

  const handleDropdownChange = (selectedOption: OptionType | null | void) => {
    if (selectedOption?.value) {
      setModel(selectedOption?.value);
    }
    setFilesData((prevfiles) => {
      return prevfiles.map((curfile) => {
        return {
          ...curfile,
          model:
            curfile.status === 'New' || curfile.status === 'Chunked' ? (selectedOption?.value ?? '') : curfile.model,
        };
      });
    });
  };
  const getChunks = async (name: string, pageNo: number) => {
    chunksTextAbortController.current = new AbortController();
    toggleChunksLoading();
    const response = await getChunkText(name, pageNo, chunksTextAbortController.current.signal);
    setTextChunks(response.data.data.pageitems);
    if (!totalPageCount) {
      setTotalPageCount(response.data.data.total_pages);
    }
    toggleChunksLoading();
  };

  const extractData = async (uid: string, isselectedRows = false, filesTobeProcess: CustomFile[]) => {
    if (!isselectedRows) {
      const fileItem = filesData.find((f) => f.id == uid);
      if (fileItem) {
        setIsExtractLoading(true);
        await extractHandler(fileItem, uid);
      }
    } else {
      const fileItem = filesTobeProcess.find((f) => f.id == uid);
      if (fileItem) {
        setIsExtractLoading(true);
        await extractHandler(fileItem, uid);
      }
    }
  };

  const extractHandler = async (fileItem: CustomFile, uid: string) => {
    queue.remove((item) => item.name === fileItem.name);

    try {
      setFilesData((prevfiles) =>
        prevfiles.map((curfile) => {
          if (curfile.id === uid) {
            return {
              ...curfile,
              status: 'Processing',
            };
          }
          return curfile;
        })
      );
      setRowSelection((prev) => {
        const copiedobj = { ...prev };
        for (const key in copiedobj) {
          if (key == uid) {
            copiedobj[key] = false;
          }
        }
        return copiedobj;
      });
      if (fileItem.name != undefined && userCredentials != null) {
        const { name } = fileItem;
        triggerStatusUpdateAPI(normalizeFileName(name) as string, userCredentials, updateStatusForLargeFiles);
      }

      const apiResponse = await extractAPI(
        fileItem.model,
        fileItem.fileSource,
        fileItem.retryOption ?? '',
        fileItem.sourceUrl,
        localStorage.getItem('accesskey'),
        atob(localStorage.getItem('secretkey') ?? ''),
        normalizeFileName(fileItem.name) ?? '', // UTF-8 normalize file name before sending to backend
        fileItem.gcsBucket ?? '',
        fileItem.gcsBucketFolder ?? '',
        selectedNodes.map((l) => l.value),
        selectedRels.map((t) => t.value),
        selectedTokenChunkSize,
        selectedChunk_overlap,
        selectedChunks_to_combine,
        fileItem.googleProjectId,
        fileItem.language,
        fileItem.accessToken,
        additionalInstructions,
        postProcessingTasks.includes('entity_relationship_post_processing'),
        JSON.stringify(entityRelationshipRules),
        selectedMaxPages
      );
      if (apiResponse?.status === 'Failed') {
        let errorobj = { error: apiResponse.error, message: apiResponse.message, fileName: apiResponse.file_name };
        throw new Error(JSON.stringify(errorobj));
      } else if (fileItem.size != undefined && fileItem.size < largeFileSize) {
        if (apiResponse.data.message) {
          const apiRes = apiResponse.data.message;
          showSuccessToast(apiRes);
        }
        setFilesData((prevfiles) => {
          return prevfiles.map((curfile) => {
            if (normalizeFileName(curfile.name) === normalizeFileName(apiResponse?.data?.fileName)) {
              const apiRes = apiResponse?.data;
              return {
                ...curfile,
                processingProgress: apiRes?.processingTime?.toFixed(2),
                processingTotalTime: apiRes?.processingTime?.toFixed(2),
                status: apiRes?.status,
                nodesCount: apiRes?.nodeCount,
                relationshipsCount: apiRes?.relationshipCount,
                model: apiRes?.model,
              };
            }
            return curfile;
          });
        });
      }
    } catch (err: any) {
      if (err instanceof Error) {
        try {
          const error = JSON.parse(err.message);
          if (Object.keys(error).includes('fileName')) {
            setProcessedCount((prev) => {
              if (prev == batchSize) {
                return batchSize - 1;
              }
              return prev + 1;
            });
            const { message, fileName } = error;
            queue.remove((item) => item.name === fileName);
            const errorMessage = error.message;
            showErrorToast(message);
            setFilesData((prevfiles) =>
              prevfiles.map((curfile) => {
                if (normalizeFileName(curfile.name) === normalizeFileName(fileName)) {
                  return { ...curfile, status: 'Failed', errorMessage };
                }
                return curfile;
              })
            );
          } else {
            // Hata formatı beklenmeyen, logging kullan
          }
        } catch (parseError) {
          if (axios.isAxiosError(err)) {
            const axiosErrorMessage = err.response?.data?.message || err.message;
            showErrorToast(axiosErrorMessage);
          } else {
            showErrorToast(err.message || 'An unexpected error occurred');
          }
        }
      } else {
        showErrorToast('An unknown error occurred');
      }
    }
  };

  const triggerBatchProcessing = (
    batch: CustomFile[],
    selectedFiles: CustomFile[],
    isSelectedFiles: boolean,
    newCheck: boolean,
    skipQueueCheck: boolean = false
  ) => {
    // Queue processing durdurulmuşsa ve bu manuel çağrı değilse işlem yapma
    if (isQueueProcessingStopped && !skipQueueCheck) {
      return [];
    }

    const data = [];
    showNormalToast(`Processing ${batch.length} files at a time.`);
    for (let i = 0; i < batch.length; i++) {
      if (newCheck) {
        if (batch[i]?.status === 'New' || batch[i].status === 'Ready to Reprocess') {
          data.push(extractData(batch[i].id, isSelectedFiles, selectedFiles as CustomFile[]));
        }
      } else {
        data.push(extractData(batch[i].id, isSelectedFiles, selectedFiles as CustomFile[]));
      }
    }
    return data;
  };

  const addFilesToQueue = async (remainingFiles: CustomFile[]) => {
    if (!remainingFiles.length && postProcessingTasks.length) {
      showNormalToast(
        <PostProcessingToast
          isGdsActive={isGdsActive}
          postProcessingTasks={postProcessingTasks}
          isSchema={hasSelections}
        />
      );
      try {
        const response = await postProcessing(postProcessingTasks);
        if (response.data.status === 'Success') {
          const communityfiles = response.data?.data;
          if (Array.isArray(communityfiles) && communityfiles.length) {
            communityfiles?.forEach((c: any) => {
              setFilesData((prev) => {
                return prev.map((f) => {
                  if (f.name === c.filename) {
                    return {
                      ...f,
                      chunkNodeCount: c.chunkNodeCount ?? 0,
                      entityNodeCount: c.entityNodeCount ?? 0,
                      communityNodeCount: c.communityNodeCount ?? 0,
                      chunkRelCount: c.chunkRelCount ?? 0,
                      entityEntityRelCount: c.entityEntityRelCount ?? 0,
                      communityRelCount: c.communityRelCount ?? 0,
                      nodesCount: c.nodeCount,
                      relationshipsCount: c.relationshipCount,
                    };
                  }
                  return f;
                });
              });
            });
          }
          showSuccessToast('Tüm Soru & Cevap fonksiyonları artık kullanılabilir.');
          // Post-processing tamamlandıktan sonra tasks'ları temizle
          // Bu sayede frontend yenilendiğinde tekrar çalışmaz
          setPostProcessingTasks([]);
        } else {
          throw new Error(response.data.error);
        }
      } catch (error) {
        // Hata durumunda da tasks'ları temizle ki tekrar çalışmasın
        setPostProcessingTasks([]);
        if (error instanceof Error) {
          showErrorToast(error.message);
        }
      }
    }
    for (let index = 0; index < remainingFiles.length; index++) {
      const f = remainingFiles[index];
      setFilesData((prev) =>
        prev.map((pf) => {
          if (pf.id === f.id) {
            return {
              ...pf,
              status: 'Waiting',
            };
          }
          return pf;
        })
      );
      queue.enqueue(f);
    }
  };

  const scheduleBatchWiseProcess = (
    selectedRows: CustomFile[],
    isSelectedFiles: boolean,
    skipQueueCheck: boolean = false
  ) => {
    // Queue processing durdurulmuşsa ve bu manuel çağrı değilse işlem yapma
    if (isQueueProcessingStopped && !skipQueueCheck) {
      return [];
    }

    let data = [];
    if (queue.size() > batchSize) {
      const batch = queue.items.slice(0, batchSize);
      data = triggerBatchProcessing(batch, selectedRows as CustomFile[], isSelectedFiles, false, skipQueueCheck);
    } else {
      let mergedfiles = [...selectedRows];
      let filesToProcess: CustomFile[] = [];
      if (mergedfiles.length > batchSize) {
        filesToProcess = mergedfiles.slice(0, batchSize);
        const remainingFiles = [...(mergedfiles as CustomFile[])].splice(batchSize);
        addFilesToQueue(remainingFiles);
      } else {
        filesToProcess = mergedfiles;
      }
      data = triggerBatchProcessing(
        filesToProcess,
        selectedRows as CustomFile[],
        isSelectedFiles,
        false,
        skipQueueCheck
      );
    }
    return data;
  };

  /**
   * Processes files in batches, respecting a maximum batch size.
   *
   * This function prioritizes processing files from the queue if it's not empty.
   * If the queue is empty, it processes the provided `filesTobeProcessed`:
   *   - If the number of files exceeds the batch size, it processes a batch and queues the rest.
   *   - If the number of files is within the batch size, it processes them all.
   *   - If there are already files being processed, it adjusts the batch size to avoid exceeding the limit.
   *
   * @param filesTobeProcessed - The files to be processed.
   * @param queueFiles - Whether to prioritize processing files from the queue. Defaults to false.
   */
  const handleGenerateGraph = (filesTobeProcessed: CustomFile[], queueFiles: boolean = false) => {
    // Graph oluştur butonuna basıldığında queue processing'i tekrar etkinleştir
    if (isQueueProcessingStopped) {
      setIsQueueProcessingStopped(false);
      showNormalToast('Queue processing resumed');
    }

    let data = [];
    const processingFilesCount = filesData.filter((f) => f.status === 'Processing').length;
    if (filesTobeProcessed.length && !queueFiles && processingFilesCount < batchSize) {
      if (!queue.isEmpty()) {
        data = scheduleBatchWiseProcess(filesTobeProcessed as CustomFile[], true, true);
      } else if (filesTobeProcessed.length > batchSize) {
        const filesToProcess = filesTobeProcessed?.slice(0, batchSize) as CustomFile[];
        data = triggerBatchProcessing(filesToProcess, filesTobeProcessed as CustomFile[], true, false, true);
        const remainingFiles = [...(filesTobeProcessed as CustomFile[])].splice(batchSize);
        addFilesToQueue(remainingFiles);
      } else {
        let filesTobeSchedule: CustomFile[] = filesTobeProcessed;
        if (filesTobeProcessed.length + processingFilesCount > batchSize) {
          filesTobeSchedule = filesTobeProcessed.slice(
            0,
            filesTobeProcessed.length + processingFilesCount - batchSize
          ) as CustomFile[];
          const idstoexclude = new Set(filesTobeSchedule.map((f) => f.id));
          const remainingFiles = [...(childRef.current?.getSelectedRows() as CustomFile[])].filter(
            (f) => !idstoexclude.has(f.id)
          );
          addFilesToQueue(remainingFiles);
        }
        data = triggerBatchProcessing(filesTobeSchedule, filesTobeProcessed, true, true, true);
      }
      Promise.allSettled(data).then((_) => {
        setIsExtractLoading(false);
      });
    } else if (queueFiles && !queue.isEmpty() && processingFilesCount < batchSize) {
      data = scheduleBatchWiseProcess(queue.items, true, true);
      Promise.allSettled(data).then((_) => {
        setIsExtractLoading(false);
      });
    } else {
      addFilesToQueue(filesTobeProcessed as CustomFile[]);
    }
  };

  const processWaitingFilesOnRefresh = useCallback(() => {
    // Queue processing durdurulmuşsa sayfa refresh'te de işlem yapma
    if (isQueueProcessingStopped) {
      return;
    }

    let data = [];
    const processingFilesCount = filesData.filter((f) => f.status === 'Processing').length;

    if (!queue.isEmpty() && processingFilesCount < batchSize) {
      if (queue.size() > batchSize) {
        const batch = queue.items.slice(0, batchSize);
        data = triggerBatchProcessing(batch, queue.items as CustomFile[], true, false, false);
      } else {
        data = triggerBatchProcessing(queue.items, queue.items as CustomFile[], true, false, false);
      }
      Promise.allSettled(data).then((_) => {
        setIsExtractLoading(false);
      });
    } else {
      const selectedNewFiles = childRef.current
        ?.getSelectedRows()
        .filter((f) => f.status === 'New' || f.status == 'Ready to Reprocess');
      addFilesToQueue(selectedNewFiles as CustomFile[]);
    }
  }, [filesData, queue, isQueueProcessingStopped]);

  const handleOpenGraphClick = () => {
    const bloomUrl = process.env.VITE_BLOOM_URL;
    let finalUrl = bloomUrl;
    if (userCredentials?.database && userCredentials.uri && userCredentials.userName) {
      const uriCoded = userCredentials.uri.replace(/:\d+$/, '');
      const connectURL = `${uriCoded.split('//')[0]}//${userCredentials.userName}@${uriCoded.split('//')[1]}:${
        userCredentials.port ?? '7687'
      }`;
      const encodedURL = encodeURIComponent(connectURL);
      finalUrl = bloomUrl?.replace('{CONNECT_URL}', encodedURL);
    }
    window.open(finalUrl, '_blank');
  };

  const handleGraphView = () => {
    setOpenGraphView(true);
    setViewPoint('showGraphView');
  };

  const handleMergeDuplicateEntities = () => {
    if (!connectionStatus) {
      showErrorToast('Lütfen önce Neo4j veritabanına bağlanın');
      return;
    }
    setOpenMergeDuplicateModal(true);
  };

  const handleCreateEmbeddingsForV2 = async () => {
    const v2Files = childRef.current?.getV2SelectedFiles?.() || [];
    const allV2Files = childRef.current?.getV2Files?.() || [];
    
    // Sadece embedding'i pending veya failed olan dosyaları filtrele (completed olanları DAHIL ETME!)
    const chunkedFiles = v2Files.filter((f: CustomFile) => 
      f.chunking_status === 'chunked' && 
      (f.embedding_status === 'pending' || f.embedding_status === 'failed')
    );

    if (!chunkedFiles || chunkedFiles.length === 0) {
      showErrorToast('Embedding oluşturmak için uygun dosya bulunamadı (pending veya failed embedding)');
      return;
    }

    try {
      setIsExtractLoading(true);
      
      // Tüm dosyalar seçilmiş mi kontrol et
      const isAllSelected = allV2Files.length > 0 && v2Files.length === allV2Files.length;
      
      let response;
      if (isAllSelected) {
        // Tüm dosyalar seçilmişse "all" parametresi kullan
        showNormalToast(`Tüm uygun dosyalar için embedding oluşturma başlatılıyor...`);
        response = await startEmbeddingAPI('all');
      } else {
        showNormalToast(`${chunkedFiles.length} dosya için embedding oluşturma başlatılıyor...`);
        
        // Seçili dosya ID'lerini virgülle ayırarak TEK bir API çağrısı yap
        const fileIds = chunkedFiles
          .filter((f: CustomFile) => f.v2FileId)
          .map((f: CustomFile) => f.v2FileId)
          .join(',');

        if (!fileIds) {
          showErrorToast('Geçerli dosya ID\'si bulunamadı');
          return;
        }

        response = await startEmbeddingAPI(fileIds as any);
      }
      
      if (response.status === 'Success' || response.status === 'success') {
        const queuedCount = response.data?.queued_count || chunkedFiles.length;
        showSuccessToast(`✓ ${queuedCount} dosya için embedding oluşturma başlatıldı`);
        
        // Dosya listesini yenile - polling FileTable tarafından otomatik yapılacak
        await new Promise((resolve) => setTimeout(resolve, 500));
        childRef.current?.reloadV2Files?.();
      } else {
        showErrorToast(`Embedding oluşturma başarısız: ${response.message || 'Bilinmeyen hata'}`);
      }
    } catch (error: any) {
      const errorMsg = error.response?.data?.message || error.message || 'Embedding hatası';
      showErrorToast(`Embedding işlemi başlatılamadı: ${errorMsg}`);
    } finally {
      setIsExtractLoading(false);
    }
  };

  const handleCreateEmbeddings = () => {
    if (!connectionStatus) {
      showErrorToast('Lütfen önce Neo4j veritabanına bağlanın');
      return;
    }
    setOpenCreateEmbeddingsModal(true);
  };

  const handleCreateEntityEmbeddings = () => {
    if (!connectionStatus) {
      showErrorToast('Lütfen önce Neo4j veritabanına bağlanın');
      return;
    }
    setOpenCreateEntityEmbeddingsModal(true);
  };

  const disconnect = () => {
    queue.clear();
    const date = new Date();
    setProcessedCount(0);
    setConnectionStatus(false);
    localStorage.removeItem('password');
    localStorage.removeItem('selectedModel');
    setUserCredentials({ uri: '', password: '', userName: '', database: '', email: '' });
    setSelectedNodes([]);
    setSelectedRels([]);
    setAllPatterns([]);
    localStorage.removeItem('selectedTokenChunkSize');
    setSelectedTokenChunkSize(tokenchunkSize);
    localStorage.removeItem('selectedChunk_overlap');
    setSelectedChunk_overlap(chunkOverlap);
    localStorage.removeItem('selectedChunks_to_combine');
    setSelectedChunks_to_combine(chunksToCombine);
    localStorage.removeItem('instructions');
    localStorage.removeItem('selectedNodeLabels');
    localStorage.removeItem('selectedRelationshipLabels');
    localStorage.removeItem('selectedPattern');
    setAdditionalInstructions('');
    setPostProcessingTasks([]);
    setMessages([
      {
        datetime: `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`,
        id: 2,
        modes: {
          'graph+vector+fulltext': {
            message:
              " Merhaba! Aktek Genai Workbench'e hoş geldiniz. Yüklenen belgelerle ilgili sorular sorabilir ve konuşmalar yapabilirsiniz.",
          },
        },
        user: 'chatbot',
        currentMode: 'graph+vector+fulltext',
      },
    ]);
    setchatModes([chatModeLables['graph+vector+fulltext']]);
  };

  const retryHandler = async (filename: string, retryoption: string) => {
    try {
      setRetryLoading(true);
      const response = await retry(filename, retryoption);
      setRetryLoading(false);
      if (response.data.status === 'Failure') {
        throw new Error(response.data.error);
      } else if (
        response.data.status === 'Success' &&
        response.data?.message != undefined &&
        (response.data?.message as string).includes('Chunks are not created')
      ) {
        showNormalToast(response.data.message as string);
        retryOnclose();
      } else {
        const isStartFromBegining = retryoption === RETRY_OPIONS[0] || retryoption === RETRY_OPIONS[1];
        setFilesData((prev) => {
          return prev.map((f) => {
            return f.name === filename
              ? {
                  ...f,
                  status: 'Ready to Reprocess',
                  processingProgress: isStartFromBegining ? 0 : f.processingProgress,
                  nodesCount: isStartFromBegining ? 0 : f.nodesCount,
                  relationshipsCount: isStartFromBegining ? 0 : f.relationshipsCount,
                }
              : f;
          });
        });
        showSuccessToast(response.data.message as string);
        retryOnclose();
      }
    } catch (error) {
      setRetryLoading(false);
      if (error instanceof Error) {
        setAlertStateForRetry({
          showAlert: true,
          alertMessage: error.message,
          alertType: 'danger',
        });
      }
    }
  };

  // Seçili dosya sayısını rowSelection ve filesData'dan hesapla
  // Sadece filesData'da var olan ve seçili olan dosyaları say
  const selectedfileslength = useMemo(() => {
    const selectedIds = Object.keys(rowSelection).filter(id => rowSelection[id]);
    // filesData'da var olan seçili dosyaları filtrele
    const validSelectedFiles = filesData.filter(f => selectedIds.includes(f.id));
    return validSelectedFiles.length;
  }, [rowSelection, filesData]);

  // Seçili dosyaları hesapla (diğer useMemo'lar için)
  const selectedFiles = useMemo(() => {
    const selectedIds = Object.keys(rowSelection).filter(id => rowSelection[id]);
    return filesData.filter(f => selectedIds.includes(f.id));
  }, [rowSelection, filesData]);

  const newFilecheck = useMemo(
    () => selectedFiles.filter((f) => f.status === 'New' || f.status === 'Ready to Reprocess' || f.status === 'Ready for Chunking').length,
    [selectedFiles]
  );

  const completedfileNo = useMemo(
    () => selectedFiles.filter((f) => f.status === 'Completed' || f.status?.includes('Completed')).length,
    [selectedFiles]
  );

  const dropdowncheck = useMemo(
    () => !filesData.some((f) => f.status === 'New' || f.status === 'Waiting' || f.status === 'Ready to Reprocess'),
    [filesData]
  );

  const disableCheck = useMemo(
    () => (!selectedfileslength ? dropdowncheck : !newFilecheck),
    [selectedfileslength, filesData, newFilecheck]
  );

  const showGraphCheck = useMemo(
    () => (selectedfileslength ? completedfileNo === 0 : true),
    [selectedfileslength, completedfileNo]
  );

  const shouldDisableChunkingButton = useMemo(() => {
    const hasV2FilesForChunking = v2FilesCategorized.pendingChunking > 0;
    const hasRegularFilesForChunking = newFilecheck && newFilecheck > 0;
    return !hasV2FilesForChunking && !hasRegularFilesForChunking;
  }, [newFilecheck, isReadOnlyUser, v2FilesCategorized.pendingChunking]);

  const shouldDisableGenerateGraphButton = useMemo(() => {
    const hasV2FilesForGraph = v2FilesCategorized.readyForGraph > 0;
    return !hasV2FilesForGraph;
  }, [isReadOnlyUser, v2FilesCategorized.readyForGraph]);

  const filesForProcessing = useMemo(() => {
    let newstatusfiles: CustomFile[] = [];
    const selectedRows = childRef.current?.getSelectedRows();
    if (selectedRows?.length) {
      for (let index = 0; index < selectedRows.length; index++) {
        const parsedFile: CustomFile = selectedRows[index];
        if (parsedFile.status === 'New' || parsedFile.status == 'Ready to Reprocess') {
          newstatusfiles.push(parsedFile);
        }
      }
    } else if (filesData.length) {
      newstatusfiles = filesData.filter((f) => f.status === 'New' || f.status === 'Ready to Reprocess');
    }
    return newstatusfiles;
  }, [filesData, childRef.current?.getSelectedRows()]);

  const deleteV2Files = async (v2Files: CustomFile[]): Promise<number> => {
    // Check if all V2 files are selected
    const allV2Files = filesData.filter((f) => f.fileSource === 'V2 Queue' && f.v2FileId);
    const isAllSelected = allV2Files.length > 0 && v2Files.length === allV2Files.length;

    if (isAllSelected) {
      // Use "all" parameter
      try {
        showNormalToast('Tüm V2 dosyaları siliniyor...');
        const response = await deleteFileFromQueueAPI('all');
        const isSuccess = response.status === 'Success' || response.status === 'success';

        if (isSuccess) {
          const deletedCount = response.data?.deleted_count || v2Files.length;
          showSuccessToast(`✓ ${deletedCount} dosya silindi`);
          return deletedCount;
        } else {
          showErrorToast(`✗ Silme işlemi başarısız: ${response.message || 'Bilinmeyen hata'}`);
          return 0;
        }
      } catch (error: any) {
        const errorMsg = error.response?.data?.message || error.message || 'Silme hatası';
        showErrorToast(`✗ Silme hatası: ${errorMsg}`);
        return 0;
      }
    }

    // Delete files individually
    let successCount = 0;
    showNormalToast(`${v2Files.length} V2 dosyası siliniyor...`);

    for (const file of v2Files) {
      try {
        const response = await deleteFileFromQueueAPI(file.v2FileId!);
        const isSuccess = response.status === 'Success' || response.status === 'success';

        if (isSuccess) {
          successCount++;
          showSuccessToast(`✓ ${file.name} silindi`);
        } else {
          showErrorToast(`✗ ${file.name} silinemedi: ${response.message || 'Bilinmeyen hata'}`);
        }
      } catch (error: any) {
        const errorMsg = error.response?.data?.message || error.message || 'Silme hatası';
        showErrorToast(`✗ ${file.name}: ${errorMsg}`);
      }
    }
    return successCount;
  };

  const deleteV1Files = async (v1Files: CustomFile[], deleteEntities: boolean): Promise<number> => {
    showNormalToast(`${v1Files.length} V1 dosyası siliniyor...`);

    const response = await deleteAPI(v1Files, deleteEntities);
    if (response.data.status === 'Success') {
      showSuccessToast(response.data.message);
      return v1Files.length;
    }

    let errorobj = { error: response.data.error, message: response.data.message };
    throw new Error(JSON.stringify(errorobj));
  };

  const handleDeleteFiles = async (deleteEntities: boolean) => {
    try {
      setIsDeleteLoading(true);
      const selectedRows = childRef.current?.getSelectedRows() as CustomFile[];

      // V2 ve V1 dosyalarını ayır
      const v2Files = selectedRows.filter((f) => f.fileSource === 'V2 Queue' && f.v2FileId);
      const v1Files = selectedRows.filter((f) => f.fileSource !== 'V2 Queue');

      let successCount = 0;
      const totalCount = selectedRows.length;

      // V2 dosyalarını sil
      if (v2Files.length > 0) {
        successCount += await deleteV2Files(v2Files);
      }

      // V1 dosyalarını sil
      if (v1Files.length > 0) {
        successCount += await deleteV1Files(v1Files, deleteEntities);
      }

      // Başarılı silinen dosyaları UI'dan kaldır
      if (successCount > 0) {
        const filenames = selectedRows.map((str) => str.name);
        filenames.forEach((name) => {
          setFilesData((prev) => prev.filter((f) => f.name !== name));
        });

        showSuccessToast(`${successCount}/${totalCount} dosya başarıyla silindi`);

        // V2 dosya listesini yenile
        if (v2Files.length > 0) {
          setTimeout(() => {
            childRef.current?.reloadV2Files?.();
          }, 500);
        }
      }

      queue.clear();
      setProcessedCount(0);
      setRowSelection({});
    } catch (err) {
      if (err instanceof Error) {
        try {
          const error = JSON.parse(err.message);
          showErrorToast(error.message);
        } catch {
          showErrorToast(err.message);
        }
      }
    } finally {
      setIsDeleteLoading(false);
      setShowDeletePopUp(false);
    }
  };

  // V2 dosyaları durumlarına göre kategorize et
  const getV2FilesCategorized = () => {
    // Seçili dosyaları al
    const v2Files = childRef.current?.getV2SelectedFiles?.() || [];

    const pendingChunking = v2Files.filter(
      (f: CustomFile) => f.upload_status === 'uploaded' && f.chunking_status === 'ready'
    ).length;

    const readyForGraph = v2Files.filter(
      (f: CustomFile) => f.chunking_status === 'chunked' && f.graph_status === 'pending'
    ).length;

    const pendingEndorsement = v2Files.filter(
      (f: CustomFile) => f.graph_status === 'pending_endorsement'
    ).length;

    const readyForEmbedding = v2Files.filter(
      (f: CustomFile) => f.chunking_status === 'chunked' && 
      (f.embedding_status === 'pending' || f.embedding_status === 'failed')
    ).length;

    return { pendingChunking, readyForGraph, pendingEndorsement, readyForEmbedding, completed: 0 };
  };

  // V2 Chunking başlatma handler'ı
  const handleStartChunkingForV2 = async () => {
    const v2Files = childRef.current?.getV2SelectedFiles?.() || [];
    const v2FileIds = childRef.current?.getV2SelectedFileIds();

    if (!v2FileIds || v2FileIds.length === 0) {
      showErrorToast('Hiçbir V2 dosyası seçilmedi');
      return;
    }

    // Check if all V2 files are selected
    const allV2Files = filesData.filter((f) => f.fileSource === 'V2 Queue' && f.v2FileId);
    const isAllSelected = allV2Files.length > 0 && v2FileIds.length === allV2Files.length;

    try {
      setIsExtractLoading(true);

      if (isAllSelected) {
        // Tüm dosyalar seçilmişse "all" parametresi kullan
        showNormalToast('Tüm dosyalar için chunking başlatılıyor...');
        const response = await startChunkingAPI('all');
        if (response.status === 'Success' || response.status === 'success' || response.data?.status === 'success') {
          const processedCount = response.data?.processed_count || v2FileIds.length;
          showSuccessToast(`✓ ${processedCount} dosya chunking'e alındı`);
          childRef.current?.reloadV2Files?.();
        } else {
          showErrorToast(`Chunking başlatılamadı: ${response.message || 'Bilinmeyen hata'}`);
        }
      } else {
        // Aradan seçim yapılmışsa, her dosya için tek tek istek gönder
        showNormalToast(`${v2FileIds.length} dosya için chunking başlatılıyor...`);
        let successCount = 0;
        let failCount = 0;
        const failedFiles: Array<{ id: number; reason: string }> = [];

        for (const fileId of v2FileIds) {
          try {
            const response = await startChunkingAPI(fileId);
            if (response.status === 'Success' || response.status === 'success' || response.data?.status === 'success') {
              successCount++;
            } else {
              failCount++;
              const errorMessage = response.message || response.error || 'Unknown error';
              failedFiles.push({ id: fileId, reason: errorMessage });
              console.error(`Failed to start chunking for file ${fileId}:`, errorMessage);
            }
          } catch (error: any) {
            failCount++;
            const errorMessage = error?.message || error?.toString() || 'Unknown error';
            failedFiles.push({ id: fileId, reason: errorMessage });
            console.error(`Error starting chunking for file ${fileId}:`, error);
          }
        }

        if (failCount === 0) {
          showSuccessToast(`✓ ${successCount} dosya chunking'e alındı`);
        } else {
          const errorDetails = failedFiles.map((f) => `Dosya ${f.id}: ${f.reason}`).join('; ');
          showErrorToast(
            `${successCount} dosya chunking'e alındı, ${failCount} dosya başarısız oldu. ${errorDetails}`
          );
        }
        childRef.current?.reloadV2Files?.();
      }
    } catch (error: any) {
      showErrorToast('Chunking işlemi başlatılamadı');
    } finally {
      setIsExtractLoading(false);
    }
  };

  // Background processor başlatma handler'ı
  const handleStartBackgroundProcessor = async () => {
    try {
      setIsExtractLoading(true);
      
      // TÜM failed dosyaları reset et (dosya seçilmeden de çalışır)
      // Backend'de "all" parametresiyle ve "invalidate" stage ile akıllı reset yapıyoruz
      showNormalToast('Takılan dosyalar kontrol ediliyor...');
      
      const { resetFileStageAPI } = await import('../utils/FileAPI');
      
      try {
        // Tek bir çağrı ile tüm takılan dosyaları (chunking, graph, embedding) resetle
        const response = await resetFileStageAPI('all', 'invalidate');
        
        if (response.status === 'Success' || response.status === 'success') {
          const resetCount = response.data?.reset_count || 0;
          if (resetCount > 0) {
            showSuccessToast(`✓ ${resetCount} dosya reset edildi ve tekrar kuyruğa alındı`);
            // Dosya listesini yenile
            await new Promise((resolve) => setTimeout(resolve, 500));
            childRef.current?.reloadV2Files?.();
          } else {
            showNormalToast('Reset edilecek takılmış dosya bulunamadı');
          }
        }
      } catch (error) {
        console.error('Failed to invalidate stuck files:', error);
        showErrorToast('Reset işlemi sırasında hata oluştu');
      }

      showNormalToast('Background processor başlatılıyor...');

      const response = await startBackgroundProcessingAPI();
      if (response.status === 'Success' || response.status === 'success') {
        if (response.data?.status === 'already_running') {
          showNormalToast('Background processor zaten çalışıyor');
          setIsBackgroundProcessorRunning(true);
        } else {
          showSuccessToast('İşlenmeyen kayıtlar Invalidate yapıldı');
          setIsBackgroundProcessorRunning(true);
        }
      } else {
        showErrorToast(`Background processor başlatılamadı: ${response.message || 'Bilinmeyen hata'}`);
      }
    } catch (error: any) {
      const errorMsg = error.response?.data?.message || error.message || 'Background processor hatası';
      showErrorToast(`Background processor başlatılamadı: ${errorMsg}`);
    } finally {
      setIsExtractLoading(false);
    }
  };

  // Background processor durumunu kontrol et
  const [isBackgroundProcessorRunning, setIsBackgroundProcessorRunning] = useState<boolean>(false);

  useEffect(() => {
    const checkBackgroundProcessorStatus = async () => {
      try {
        const response = await getBackgroundProcessingStatusAPI();
        if (response?.status === 'Success') {
          const status = response?.data?.processing_status;
          setIsBackgroundProcessorRunning(status?.is_processing || false);
        }
      } catch (error) {
        // Silently fail
      }
    };

    // İlk yüklemede bir kez kontrol et
    checkBackgroundProcessorStatus();

    // Watch mode aktifse periyodik polling yap
    if (!watchProcessingMode) {
      return;
    }
    
    const interval = setInterval(checkBackgroundProcessorStatus, 5000); // Her 5 saniyede bir kontrol et
    return () => clearInterval(interval);
  }, [watchProcessingMode]);

  // V2 Chunking reset handler'ı
  const handleResetChunkingForV2 = async () => {
    const v2FileIds = childRef.current?.getV2SelectedFileIds();

    if (!v2FileIds || v2FileIds.length === 0) {
      showErrorToast('Hiçbir V2 dosyası seçilmedi');
      return;
    }

    // İlk confirm: Reset işlemini onaylama
    if (!confirm('Seçili dosyaların chunking işlemini sıfırlamak istediğinize emin misiniz?')) {
      return;
    }

    // İkinci confirm: Markdown dosyalarını silme seçeneği
    const deleteMarkdown = confirm(
      'Çıkartılan markdown dosyalarını da silmek istiyor musunuz?\n\n' +
      '• EVET: Markdown dosyaları silinir, chunking baştan yapılır\n' +
      '• HAYIR: Markdown dosyaları korunur, sadece durum sıfırlanır'
    );

    // Check if all V2 files are selected
    const allV2Files = filesData.filter((f) => f.fileSource === 'V2 Queue' && f.v2FileId);
    const isAllSelected = allV2Files.length > 0 && v2FileIds.length === allV2Files.length;

    try {
      setIsExtractLoading(true);

      if (isAllSelected) {
        // Use "all" parameter when all files are selected
        showNormalToast(`Tüm dosyalar için chunking reset ediliyor${deleteMarkdown ? ' (markdown silinecek)' : ''}...`);
        const response = await resetFileStageAPI('all', 'chunking', deleteMarkdown);
        if (response.status === 'Success' || response.status === 'success') {
          const resetCount = response.data?.reset_count || v2FileIds.length;
          showSuccessToast(`✓ ${resetCount} dosya chunking reset edildi${deleteMarkdown ? ' (markdown silindi)' : ''}`);
          childRef.current?.reloadV2Files?.();
        } else {
          showErrorToast(`Chunking reset başarısız: ${response.message || 'Bilinmeyen hata'}`);
        }
      } else {
        // Seçili dosyalar için 50'lik batch'ler halinde paralel çağrı yap
        const BATCH_SIZE = 50;
        const totalFiles = v2FileIds.length;
        const totalBatches = Math.ceil(totalFiles / BATCH_SIZE);
        
        showNormalToast(`${totalFiles} dosya için chunking reset ediliyor (${totalBatches} batch)${deleteMarkdown ? ' (markdown silinecek)' : ''}...`);
        
        let successCount = 0;
        let failCount = 0;

        // Dosyaları 50'lik batch'lere böl
        for (let batchIndex = 0; batchIndex < totalBatches; batchIndex++) {
          const start = batchIndex * BATCH_SIZE;
          const end = Math.min(start + BATCH_SIZE, totalFiles);
          const batchFileIds = v2FileIds.slice(start, end);
          
          console.log(`📦 Batch ${batchIndex + 1}/${totalBatches}: ${batchFileIds.length} dosya işleniyor...`);
          
          // Batch içindeki tüm istekleri paralel olarak gönder
          const batchPromises = batchFileIds.map(async (fileId) => {
            try {
              const response = await resetFileStageAPI(fileId, 'chunking', deleteMarkdown);
              if (response.status === 'Success' || response.status === 'success') {
                return { success: true, fileId };
              } else {
                return { success: false, fileId, error: response.message || 'Bilinmeyen hata' };
              }
            } catch (error: any) {
              const errorMsg = error.response?.data?.message || error.message || 'Reset hatası';
              return { success: false, fileId, error: errorMsg };
            }
          });

          // Batch sonuçlarını bekle
          const batchResults = await Promise.all(batchPromises);
          
          // Sonuçları say
          batchResults.forEach((result) => {
            if (result.success) {
              successCount++;
            } else {
              failCount++;
              console.error(`❌ Dosya ${result.fileId}: ${result.error}`);
            }
          });
          
          // Progress göster
          if (totalBatches > 1) {
            showNormalToast(`Batch ${batchIndex + 1}/${totalBatches} tamamlandı (${successCount}/${totalFiles})`);
          }
        }

        if (successCount > 0) {
          showSuccessToast(`✓ ${successCount}/${totalFiles} dosya chunking reset edildi${deleteMarkdown ? ' (markdown silindi)' : ''}${failCount > 0 ? ` (${failCount} başarısız)` : ''}`);
        }
        if (failCount > 0 && successCount === 0) {
          showErrorToast(`Tüm dosyalar başarısız oldu (${failCount} hata)`);
        }
        childRef.current?.reloadV2Files?.();
      }
    } catch (error: any) {
      showErrorToast('Reset işlemi başlatılamadı');
    } finally {
      setIsExtractLoading(false);
      // File table'ı güncelle
      setTimeout(() => {
        childRef.current?.reloadV2Files?.();
      }, 500);
    }
  };

  // Graph Reset handler - Entity'leri sil, yeniden extraction için hazırla
  const handleResetGraphForV2 = async () => {
    const v2FileIds = childRef.current?.getV2SelectedFileIds();

    if (!v2FileIds || v2FileIds.length === 0) {
      showErrorToast('Hiçbir V2 dosyası seçilmedi');
      return;
    }

    // Confirm: Entity'lerin silineceğini belirt
    if (!confirm(
      'Seçili dosyaların graph\'ını sıfırlamak istediğinize emin misiniz?\n\n' +
      '⚠️ Bu işlem:\n' +
      '• Neo4j\'deki TÜM entity\'leri siler (Person, Company, Meeting vb.)\n' +
      '• Document ve Chunk node\'ları KORUNUR\n' +
      '• Entity extraction yeniden çalıştırılabilir hale gelir'
    )) {
      return;
    }

    // Check if all V2 files are selected
    const allV2Files = filesData.filter((f) => f.fileSource === 'V2 Queue' && f.v2FileId);
    const isAllSelected = allV2Files.length > 0 && v2FileIds.length === allV2Files.length;

    try {
      setIsExtractLoading(true);

      if (isAllSelected) {
        // Use "all" parameter when all files are selected
        showNormalToast('Tüm dosyalar için graph reset ediliyor...');
        const response = await resetFileStageAPI('all', 'graph');
        if (response.status === 'Success' || response.status === 'success') {
          const resetCount = response.data?.reset_count || v2FileIds.length;
          showSuccessToast(`✓ ${resetCount} dosya graph reset edildi (entity\'ler silindi)`);
          childRef.current?.reloadV2Files?.();
        } else {
          showErrorToast(`Graph reset başarısız: ${response.message || 'Bilinmeyen hata'}`);
        }
      } else {
        // Seçili dosyalar için batch reset
        const BATCH_SIZE = 50;
        const totalFiles = v2FileIds.length;
        const totalBatches = Math.ceil(totalFiles / BATCH_SIZE);
        
        showNormalToast(`${totalFiles} dosya için graph reset ediliyor (${totalBatches} batch)...`);
        
        let successCount = 0;
        let failCount = 0;

        for (let batchIndex = 0; batchIndex < totalBatches; batchIndex++) {
          const batchStart = batchIndex * BATCH_SIZE;
          const batchEnd = Math.min(batchStart + BATCH_SIZE, totalFiles);
          const batchFileIds = v2FileIds.slice(batchStart, batchEnd);
          
          const batchPromises = batchFileIds.map(async (fileId) => {
            try {
              const response = await resetFileStageAPI(fileId, 'graph');
              if (response.status === 'Success' || response.status === 'success') {
                return { success: true, fileId };
              } else {
                return { success: false, fileId, error: response.message };
              }
            } catch (error: any) {
              return { success: false, fileId, error: error.message };
            }
          });

          const batchResults = await Promise.all(batchPromises);
          
          batchResults.forEach((result) => {
            if (result.success) {
              successCount++;
            } else {
              failCount++;
            }
          });
          
          if (totalBatches > 1) {
            showNormalToast(`Batch ${batchIndex + 1}/${totalBatches} tamamlandı (${successCount}/${totalFiles})`);
          }
        }

        if (successCount > 0) {
          showSuccessToast(`✓ ${successCount}/${totalFiles} dosya graph reset edildi${failCount > 0 ? ` (${failCount} başarısız)` : ''}`);
        }
        if (failCount > 0 && successCount === 0) {
          showErrorToast(`Tüm dosyalar başarısız oldu (${failCount} hata)`);
        }
        childRef.current?.reloadV2Files?.();
      }
    } catch (error: any) {
      showErrorToast('Graph reset işlemi başlatılamadı');
    } finally {
      setIsExtractLoading(false);
      setTimeout(() => {
        childRef.current?.reloadV2Files?.();
      }, 500);
    }
  };

  const onClickHandler = () => {
    const selectedRows = childRef.current?.getSelectedRows();
    const v2FileIds = childRef.current?.getV2SelectedFileIds();

    // Handle V2 files separately
    if (v2FileIds && v2FileIds.length > 0) {
      childRef.current?.handleCreateGraph();
      return;
    }

    if (selectedRows?.length) {
      const expiredFilesExists = selectedRows.some(
        (c) => isFileReadyToProcess(c, true) && isExpired((c?.createdAt as Date) ?? new Date())
      );
      const largeFileExists = selectedRows.some(
        (c) => isFileReadyToProcess(c, true) && typeof c.size === 'number' && c.size > largeFileSize
      );
      if (expiredFilesExists) {
        setShowExpirationModal(true);
      } else if (largeFileExists && isGCSActive) {
        setShowConfirmationModal(true);
      } else {
        handleGenerateGraph(selectedRows.filter((f) => isFileReadyToProcess(f, false)));
      }
    } else if (filesData.length) {
      const expiredFileExists = filesData.some((c) => isFileReadyToProcess(c, true) && isExpired(c?.createdAt as Date));
      const largeFileExists = filesData.some(
        (c) => isFileReadyToProcess(c, true) && typeof c.size === 'number' && c.size > largeFileSize
      );
      const selectAllNewFiles = filesData.filter((f) => isFileReadyToProcess(f, false));
      const stringified = selectAllNewFiles.reduce((accu, f) => {
        const key = f.id;
        // @ts-ignore
        accu[key] = true;
        return accu;
      }, {});
      setRowSelection(stringified);
      if (largeFileExists) {
        setShowConfirmationModal(true);
      } else if (expiredFileExists && isGCSActive) {
        setShowExpirationModal(true);
      } else {
        handleGenerateGraph(filesData.filter((f) => isFileReadyToProcess(f, false)));
      }
    }
  };

  const retryOnclose = useCallback(() => {
    setRetryFile('');
    setAlertStateForRetry({
      showAlert: false,
      alertMessage: '',
      alertType: 'neutral',
    });
    setRetryLoading(false);
    toggleRetryPopup();
  }, []);

  const onBannerClose = useCallback(() => {
    setAlertStateForRetry({
      showAlert: false,
      alertMessage: '',
      alertType: 'neutral',
    });
  }, []);

  const handleSchemaView = () => {
    setOpenGraphView(true);
    setViewPoint('showSchemaView');
  };

  return (
    <>
      <RetryConfirmationDialog
        retryLoading={retryLoading}
        retryHandler={retryHandler}
        fileId={retryFile}
        onClose={retryOnclose}
        open={showRetryPopup}
        onBannerClose={onBannerClose}
        alertStatus={alertStateForRetry}
      />
      {showConfirmationModal && filesForProcessing.length && (
        <Suspense fallback={<FallBackDialog />}>
          <ConfirmationDialog
            open={showConfirmationModal}
            largeFiles={filesForProcessing}
            extractHandler={handleGenerateGraph}
            onClose={() => setShowConfirmationModal(false)}
            loading={extractLoading}
            selectedRows={childRef.current?.getSelectedRows() as CustomFile[]}
            isLargeDocumentAlert={true}
          ></ConfirmationDialog>
        </Suspense>
      )}
      {showExpirationModal && filesForProcessing.length && (
        <Suspense fallback={<FallBackDialog />}>
          <ConfirmationDialog
            open={showExpirationModal}
            largeFiles={filesForProcessing}
            extractHandler={handleGenerateGraph}
            onClose={() => setShowExpirationModal(false)}
            loading={extractLoading}
            selectedRows={childRef.current?.getSelectedRows() as CustomFile[]}
            isLargeDocumentAlert={false}
          ></ConfirmationDialog>
        </Suspense>
      )}
      {showExpirationModal && filesForProcessing.length && (
        <Suspense fallback={<FallBackDialog />}>
          <ConfirmationDialog
            open={showExpirationModal}
            largeFiles={filesForProcessing}
            extractHandler={handleGenerateGraph}
            onClose={() => setShowExpirationModal(false)}
            loading={extractLoading}
            selectedRows={childRef.current?.getSelectedRows() as CustomFile[]}
            isLargeDocumentAlert={false}
          ></ConfirmationDialog>
        </Suspense>
      )}
      {showExpirationModal && filesForProcessing.length && (
        <Suspense fallback={<FallBackDialog />}>
          <ConfirmationDialog
            open={showExpirationModal}
            largeFiles={filesForProcessing}
            extractHandler={handleGenerateGraph}
            onClose={() => setShowExpirationModal(false)}
            loading={extractLoading}
            selectedRows={childRef.current?.getSelectedRows() as CustomFile[]}
            isLargeDocumentAlert={false}
          ></ConfirmationDialog>
        </Suspense>
      )}
      {showDeletePopUp && (
        <DeletePopUp
          open={showDeletePopUp}
          no_of_files={selectedfileslength ?? 0}
          deleteHandler={(delentities: boolean) => handleDeleteFiles(delentities)}
          deleteCloseHandler={() => setShowDeletePopUp(false)}
          loading={deleteLoading}
          view='contentView'
        ></DeletePopUp>
      )}
      {showChunkPopup && (
        <ChunkPopUp
          chunksLoading={chunksLoading}
          onClose={() => {
            chunksTextAbortController.current?.abort();
            toggleChunkPopup();
          }}
          showChunkPopup={showChunkPopup}
          chunks={textChunks}
          incrementPage={incrementPage}
          decrementPage={decrementPage}
          currentPage={currentPage}
          totalPageCount={totalPageCount}
        ></ChunkPopUp>
      )}
      {showEnhancementDialog && (
        <GraphEnhancementDialog
          open={showEnhancementDialog}
          onClose={toggleEnhancementDialog}
          combinedPatterns={combinedPatterns}
          setCombinedPatterns={setCombinedPatterns}
          combinedNodes={combinedNodes}
          setCombinedNodes={setCombinedNodes}
          combinedRels={combinedRels}
          setCombinedRels={setCombinedRels}
        ></GraphEnhancementDialog>
      )}
      <GraphViewModal
        inspectedName={inspectedName}
        open={openGraphView}
        setGraphViewOpen={setOpenGraphView}
        viewPoint={viewPoint}
        selectedRows={childRef.current?.getSelectedRows()}
      />
      <MergeDuplicateEntitiesModal open={openMergeDuplicateModal} onClose={() => setOpenMergeDuplicateModal(false)} />
      <CreateEmbeddingsModal open={openCreateEmbeddingsModal} onClose={() => setOpenCreateEmbeddingsModal(false)} />
      <CreateEntityEmbeddingsModal
        open={openCreateEntityEmbeddingsModal}
        onClose={() => setOpenCreateEntityEmbeddingsModal(false)}
      />
      <div className={`n-bg-palette-neutral-bg-default main-content-wrapper`}>
        <Flex
          className='w-full absolute top-0'
          alignItems='center'
          justifyContent='space-between'
          flexDirection='row'
          flexWrap='wrap'
        >
          <div className='connectionstatus__container'>
            <span className='h6 px-1'>Graph connection {isReadOnlyUser ? '(Read only Mode)' : ''}</span>
            <Typography variant='body-medium'>
              <DatabaseStatusIcon
                isConnected={connectionStatus}
                isGdsActive={isGdsActive}
                uri={userCredentials?.uri}
                database={userCredentials?.database}
              />
              <div className='pt-1 flex! gap-1 items-center'>
                <div>{!hasSelections ? <StatusIndicator type='danger' /> : <StatusIndicator type='success' />}</div>
                <div>
                  {hasSelections ? (
                    <span className='n-body-small'>
                      {hasSelections} Graph Schema configured
                      {hasSelections ? `(${selectedNodes.length} Labels + ${selectedRels.length} Rel Types)` : ''}
                    </span>
                  ) : (
                    <span className='n-body-small'>No Graph Schema configured</span>
                  )}
                </div>
              </div>
            </Typography>
          </div>
          <div className='enhancement-btn__wrapper'>
            <TextInput
              aria-label='Dosya adı ile ara'
              placeholder='Dosya Adı ile Ara...'
              value={nameFilter}
              onChange={(e) => setNameFilter(e.target.value)}
              size='medium'
              className='mr-4! w-80'
              leftIcon={<MagnifyingGlassIconOutline className='n-size-token-5' />}
              rightIcon={
                nameFilter ? (
                  <XMarkIconOutline 
                    className='n-size-token-5 cursor-pointer' 
                    onClick={() => setNameFilter('')}
                  />
                ) : undefined
              }
            />
            <ButtonWithToolTip
              placement='top'
              text='Enhance graph quality'
              label='Graph Enhancemnet Settings'
              className='mr-2!'
              onClick={toggleEnhancementDialog}
              disabled={!connectionStatus || isReadOnlyUser}
              size={isTablet ? 'small' : 'medium'}
            >
              Graph Enhancement
            </ButtonWithToolTip>
            <ButtonWithToolTip
              placement='top'
              text='Sistem kaynak kullanımını izle'
              label='System Monitor'
              className='mr-2!'
              onClick={() => setShowSystemMonitorModal(true)}
              size={isTablet ? 'small' : 'medium'}
            >
              <ServerStackIconOutline className="n-size-token-6" />
            </ButtonWithToolTip>
            <ButtonWithToolTip
              placement='top'
              text='View Processing Statistics'
              label='View Stats'
              className='mr-2!'
              onClick={() => setShowStatsModal(true)}
              disabled={!connectionStatus}
              size={isTablet ? 'small' : 'medium'}
            >
              <ChartBarIconOutline className="n-size-token-6" />
            </ButtonWithToolTip>
            {!connectionStatus ? (
              <SpotlightTarget
                id='connectbutton'
                hasPulse={true}
                indicatorVariant='border'
                className='n-bg-palette-primary-bg-strong hover:n-bg-palette-primary-hover-strong'
              >
                <Button
                  size={isTablet ? 'small' : 'medium'}
                  className='mr-2!'
                  onClick={() => setOpenConnection((prev) => ({ ...prev, openPopUp: true }))}
                >
                  {buttonCaptions.connectToNeo4j}
                </Button>
              </SpotlightTarget>
            ) : (
              showDisconnectButton && (
                <Button size={isTablet ? 'small' : 'medium'} className='mr-2.5' onClick={disconnect}>
                  {buttonCaptions.disconnect}
                </Button>
              )
            )}
          </div>
        </Flex>



        <ProcessingStats files={filesData} open={showStatsModal} onClose={() => setShowStatsModal(false)} />

        <SystemMonitorModal open={showSystemMonitorModal} onClose={() => setShowSystemMonitorModal(false)} />

        <FileTable
          connectionStatus={connectionStatus}
          setConnectionStatus={setConnectionStatus}
          onInspect={useCallback((name) => {
            setInspectedName(name);
            setOpenGraphView(true);
            setViewPoint('tableView');
          }, [])}
          onRetry={useCallback((id) => {
            setRetryFile(id);
            toggleRetryPopup();
          }, [])}
          onChunkView={useCallback(
            async (name) => {
              setDocumentName(name);
              if (name != documentName) {
                toggleChunkPopup();
                if (totalPageCount) {
                  setTotalPageCount(null);
                }
                setCurrentPage(1);
                await getChunks(name, 1);
              }
            },
            [documentName, totalPageCount]
          )}
          ref={childRef}
          handleGenerateGraph={processWaitingFilesOnRefresh}
          setIsQueueProcessingStopped={setIsQueueProcessingStopped}
          nameFilter={nameFilter}
          setNameFilter={setNameFilter}
        ></FileTable>

        <Flex className={`p-2.5  mt-1.5 absolute bottom-0 w-full`} justifyContent='space-between' flexDirection={'row'}>
          <div>
            <DropdownComponent
              onSelect={handleDropdownChange}
              options={llms ?? ['']}
              placeholder='Select LLM Model'
              defaultValue={model}
              view='ContentView'
              isDisabled={false}
            />
          </div>
          <div className='flex flex-row items-center gap-4 mb-2'>
            <TextInput
              placeholder='Max sayfa sayısı (boş = tümü)'
              label='Max Sayfa'
              value={selectedMaxPages?.toString() || ''}
              onChange={(e) => {
                const { value } = e.target;
                setSelectedMaxPages(value ? parseInt(value) : undefined);
              }}
              className='w-48'
              size={isTablet ? 'small' : 'medium'}
              style={{ display: 'none' }}
            />
            <Checkbox
              label='Upload sırasında embedding oluştur'
              isChecked={generateEmbedding}
              onChange={(e) => setGenerateEmbedding(e.target.checked)}
              style={{ display: 'none' }}
            />
          </div>
          <Flex flexDirection='row' gap='4' className='self-end mb-2.5' flexWrap='wrap'>
            <SpotlightTarget id='chunkingbtn' borderRadius={0}>
              <Flex flexDirection='row' gap='0'>
                <Button
                  onClick={() => {
                    const cat = getV2FilesCategorized();
                    if (cat.pendingChunking > 0) {
                      handleStartChunkingForV2();
                    } else {
                      showErrorToast('Chunking yapılacak dosya yok');
                    }
                  }}
                  isDisabled={shouldDisableChunkingButton}
                  className='px-0! flex! items-center justify-between gap-4 graphbtn chunkingbtn'
                  size={isTablet ? 'small' : 'medium'}
                >
                  <span className='mx-2'>
                    {(() => {
                      const cat = getV2FilesCategorized();
                      return cat.pendingChunking > 0 ? `Chunking Başlat (${cat.pendingChunking})` : 'Chunking Başlat';
                    })()}
                  </span>
                </Button>
                <div
                  className={`ndl-icon-btn ndl-clean dropdownbtn ${colorMode === 'dark' ? 'darktheme' : ''} ${
                    isTablet ? 'small' : 'medium'
                  }`}
                  onClick={(e) => {
                    setIsChunkingBtnMenuOpen((old) => !old);
                    e.stopPropagation();
                  }}
                  ref={chunkingbtnRef}
                >
                  {!isChunkingBtnMenuOpen ? (
                    <ChevronUpIconOutline className='n-size-token-5' />
                  ) : (
                    <ChevronDownIconOutline className='n-size-token-' />
                  )}
                </div>
              </Flex>
            </SpotlightTarget>
            <Menu
              placement='top-end-bottom-end'
              isOpen={isChunkingBtnMenuOpen}
              anchorRef={chunkingbtnRef}
              onClose={() => setIsChunkingBtnMenuOpen(false)}
            >
              <Menu.Items
                htmlAttributes={{
                  id: 'chunking-menu',
                }}
              >
                <Menu.Item
                  title='Chunking Başlat'
                  onClick={() => {
                    const cat = getV2FilesCategorized();
                    if (cat.pendingChunking > 0) {
                      handleStartChunkingForV2();
                    } else {
                      showErrorToast('Chunking yapılacak dosya yok');
                    }
                  }}
                  isDisabled={isReadOnlyUser || extractLoading || getV2FilesCategorized().pendingChunking === 0}
                />
                <Menu.Item
                  title='Chunking Sıfırla'
                  onClick={() => {
                    const v2FileIds = childRef.current?.getV2SelectedFileIds();
                    if (!v2FileIds || v2FileIds.length === 0) {
                      showErrorToast('Reset için dosya seçiniz');
                    } else {
                      handleResetChunkingForV2();
                    }
                  }}
                  isDisabled={isReadOnlyUser || extractLoading}
                />
                <Menu.Item
                  title='Graph Sıfırla'
                  onClick={() => {
                    const v2FileIds = childRef.current?.getV2SelectedFileIds();
                    if (!v2FileIds || v2FileIds.length === 0) {
                      showErrorToast('Graph reset için dosya seçiniz');
                    } else {
                      handleResetGraphForV2();
                    }
                  }}
                  isDisabled={isReadOnlyUser || extractLoading}
                />
                <Menu.Divider />
                <Menu.Item
                  title='İlk 5 Ready Seç (Test için)'
                  onClick={() => {
                    const total = childRef.current?.getV2FileCount?.('ready') || 0;
                    const count = childRef.current?.selectFirstN(5, 'ready');
                    showNormalToast(`${count}/${total} ready dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 100 Ready Seç (Chunking için)'
                  onClick={() => {
                    const total = childRef.current?.getV2FileCount?.('ready') || 0;
                    const count = childRef.current?.selectFirstN(100, 'ready');
                    showNormalToast(`${count}/${total} ready dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 500 Ready Seç (Chunking için)'
                  onClick={() => {
                    const total = childRef.current?.getV2FileCount?.('ready') || 0;
                    const count = childRef.current?.selectFirstN(500, 'ready');
                    showNormalToast(`${count}/${total} ready dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 1000 Ready Seç (Chunking için)'
                  onClick={() => {
                    const total = childRef.current?.getV2FileCount?.('ready') || 0;
                    const count = childRef.current?.selectFirstN(1000, 'ready');
                    showNormalToast(`${count}/${total} ready dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 2000 Ready Seç (Chunking için)'
                  onClick={() => {
                    const total = childRef.current?.getV2FileCount?.('ready') || 0;
                    const count = childRef.current?.selectFirstN(2000, 'ready');
                    showNormalToast(`${count}/${total} ready dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 5000 Ready Seç (Chunking için)'
                  onClick={() => {
                    const total = childRef.current?.getV2FileCount?.('ready') || 0;
                    const count = childRef.current?.selectFirstN(5000, 'ready');
                    showNormalToast(`${count}/${total} ready dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 10000 Ready Seç (Chunking için)'
                  onClick={() => {
                    const total = childRef.current?.getV2FileCount?.('ready') || 0;
                    const count = childRef.current?.selectFirstN(10000, 'ready');
                    showNormalToast(`${count}/${total} ready dosya seçildi`);
                  }}
                />
                <Menu.Divider />
                <Menu.Item
                  title='İlk 5 Henüz İşlenmemiş Seç (Test için)'
                  onClick={() => {
                    // Henüz reset edilmemiş: chunked + embedding completed
                    const total = childRef.current?.getV2FileCount?.('chunked', 'completed') || 0;
                    const count = childRef.current?.selectFirstN(5, 'chunked', 'completed');
                    showNormalToast(`${count}/${total} işlenmemiş dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 100 Henüz İşlenmemiş Seç (Reset için)'
                  onClick={() => {
                    // Henüz reset edilmemiş: chunked + embedding completed
                    const total = childRef.current?.getV2FileCount?.('chunked', 'completed') || 0;
                    const count = childRef.current?.selectFirstN(100, 'chunked', 'completed');
                    showNormalToast(`${count}/${total} işlenmemiş dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 500 Henüz İşlenmemiş Seç (Reset için)'
                  onClick={() => {
                    // Henüz reset edilmemiş: chunked + embedding completed
                    const total = childRef.current?.getV2FileCount?.('chunked', 'completed') || 0;
                    const count = childRef.current?.selectFirstN(500, 'chunked', 'completed');
                    showNormalToast(`${count}/${total} işlenmemiş dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 1000 Henüz İşlenmemiş Seç (Reset için)'
                  onClick={() => {
                    // Henüz reset edilmemiş: chunked + embedding completed
                    const total = childRef.current?.getV2FileCount?.('chunked', 'completed') || 0;
                    const count = childRef.current?.selectFirstN(1000, 'chunked', 'completed');
                    showNormalToast(`${count}/${total} işlenmemiş dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 2000 Henüz İşlenmemiş Seç (Reset için)'
                  onClick={() => {
                    // Henüz reset edilmemiş: chunked + embedding completed
                    const total = childRef.current?.getV2FileCount?.('chunked', 'completed') || 0;
                    const count = childRef.current?.selectFirstN(2000, 'chunked', 'completed');
                    showNormalToast(`${count}/${total} işlenmemiş dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 5000 Henüz İşlenmemiş Seç (Reset için)'
                  onClick={() => {
                    // Henüz reset edilmemiş: chunked + embedding completed
                    const total = childRef.current?.getV2FileCount?.('chunked', 'completed') || 0;
                    const count = childRef.current?.selectFirstN(5000, 'chunked', 'completed');
                    showNormalToast(`${count}/${total} işlenmemiş dosya seçildi`);
                  }}
                />
                <Menu.Item
                  title='İlk 10000 Henüz İşlenmemiş Seç (Reset için)'
                  onClick={() => {
                    // Henüz reset edilmemiş: chunked + embedding completed
                    const total = childRef.current?.getV2FileCount?.('chunked', 'completed') || 0;
                    const count = childRef.current?.selectFirstN(10000, 'chunked', 'completed');
                    showNormalToast(`${count}/${total} işlenmemiş dosya seçildi`);
                  }}
                />
                <Menu.Divider />
                <Menu.Item
                  title='Seçimi Temizle'
                  onClick={() => {
                    childRef.current?.clearSelection();
                    showNormalToast('Seçim temizlendi');
                  }}
                />
              </Menu.Items>
            </Menu>
            <SpotlightTarget id='generategraphbtn'>
              <ButtonWithToolTip
                text={tooltips.generateGraph}
                placement='top'
                label='generate graph'
                onClick={() => {
                  // Direct hesapla
                  const categorized = getV2FilesCategorized();
                  if (categorized.readyForGraph > 0) {
                    onClickHandler();
                  } else {
                    showErrorToast('Graph oluşturmak için chunked dosya seçiniz');
                  }
                }}
                disabled={shouldDisableGenerateGraphButton}
                className='mr-0.5'
                size={isTablet ? 'small' : 'medium'}
              >
                {buttonCaptions.generateGraph}{' '}
                {(() => {
                  const cat = getV2FilesCategorized();
                  return cat.readyForGraph > 0
                    ? `(${cat.readyForGraph})`
                    : selectedfileslength && !disableCheck && newFilecheck
                      ? `(${newFilecheck})`
                      : '';
                })()}
              </ButtonWithToolTip>
            </SpotlightTarget>
            <ButtonWithToolTip
              text={
                !selectedfileslength ? tooltips.deleteFile : `${selectedfileslength} ${tooltips.deleteSelectedFiles}`
              }
              placement='top'
              onClick={() => setShowDeletePopUp(true)}
              disabled={!selectedfileslength || isReadOnlyUser}
              className='ml-0.5'
              label='Delete Files'
              size={isTablet ? 'small' : 'medium'}
            >
              {buttonCaptions.deleteFiles}
              {selectedfileslength != undefined && selectedfileslength > 0 && `(${selectedfileslength})`}
            </ButtonWithToolTip>
            <ButtonWithToolTip
              text='Seçili completed dosyaları embedding oluştur'
              placement='top'
              onClick={handleCreateEmbeddingsForV2}
              disabled={!getV2FilesCategorized().readyForEmbedding || isReadOnlyUser || extractLoading}
              className='ml-0.5'
              label='Create Embeddings'
              size={isTablet ? 'small' : 'medium'}
            >
              Create Embeddings{' '}
              {(() => {
                const cat = getV2FilesCategorized();
                return cat.readyForEmbedding > 0 ? `(${cat.readyForEmbedding})` : '';
              })()}
            </ButtonWithToolTip>
            <ButtonWithToolTip
              text='Seçili endorsement dosyaları için graph oluştur (Zeyilname, İptal, Yenileme)'
              placement='top'
              onClick={async () => {
                try {
                  setIsExtractLoading(true);
                  const categorized = getV2FilesCategorized();
                  if (categorized.pendingEndorsement > 0) {
                    showNormalToast('Endorsement dosyaları için graph oluşturuluyor...');
                    const response = await startEndorsementGraphCreationAPI('all', 'openai_gpt_4o_mini', false);
                    if (response.status === 'Success' || response.status === 'success') {
                      const processedCount = response.data?.processed_count || categorized.pendingEndorsement;
                      showSuccessToast(`✓ ${processedCount} endorsement dosyası için graph oluşturma başlatıldı`);
                      
                      // İlk refresh - processing status'ünü görmek için
                      await new Promise((resolve) => setTimeout(resolve, 200));
                      childRef.current?.reloadV2Files?.();
                      
                      // İkinci refresh - emin olmak için
                      await new Promise((resolve) => setTimeout(resolve, 500));
                      childRef.current?.reloadV2Files?.();
                    } else {
                      showErrorToast(`Endorsement graph oluşturma başarısız: ${response.message || 'Bilinmeyen hata'}`);
                    }
                  } else {
                    showErrorToast('Endorsement graph oluşturmak için pending endorsement dosyası seçiniz');
                  }
                } catch (error: any) {
                  showErrorToast('Endorsement graph oluşturma başlatılamadı');
                } finally {
                  setIsExtractLoading(false);
                }
              }}
              disabled={!getV2FilesCategorized().pendingEndorsement || isReadOnlyUser || extractLoading}
              className='ml-0.5'
              label='Create Endorsement Graph'
              size={isTablet ? 'small' : 'medium'}
            >
              Endorsement Graph Oluştur{' '}
              {(() => {
                const cat = getV2FilesCategorized();
                return cat.pendingEndorsement > 0 ? `(${cat.pendingEndorsement})` : '';
              })()}
            </ButtonWithToolTip>
            <ButtonWithToolTip
              text={isBackgroundProcessorRunning ? 'Background processor çalışıyor' : 'Yarıda kalan işlemleri resetle ve processor başlat'}
              placement='top'
              onClick={handleStartBackgroundProcessor}
              disabled={isBackgroundProcessorRunning || isReadOnlyUser || extractLoading}
              className='ml-0.5'
              label='Reset Processing'
              size={isTablet ? 'small' : 'medium'}
            >
              {isBackgroundProcessorRunning ? '🟢 Processor Running' : '🔄 Reset Processing'}
            </ButtonWithToolTip>
            <SpotlightTarget id='visualizegraphbtn'>
              <Flex flexDirection='row' gap='0'>
                <Button
                  onClick={handleGraphView}
                  isDisabled={showGraphCheck}
                  className='px-0! flex! items-center justify-between gap-4 graphbtn'
                  size={isTablet ? 'small' : 'medium'}
                >
                  <span className='mx-2'>
                    {buttonCaptions.showPreviewGraph}{' '}
                    {selectedfileslength && completedfileNo ? `(${completedfileNo})` : ''}
                  </span>
                </Button>
                <div
                  className={`ndl-icon-btn ndl-clean dropdownbtn ${colorMode === 'dark' ? 'darktheme' : ''} ${
                    isTablet ? 'small' : 'medium'
                  }`}
                  onClick={(e) => {
                    setIsGraphBtnMenuOpen((old) => !old);
                    e.stopPropagation();
                  }}
                  ref={graphbtnRef}
                >
                  {!isGraphBtnMenuOpen ? (
                    <ChevronUpIconOutline className='n-size-token-5' />
                  ) : (
                    <ChevronDownIconOutline className='n-size-token-' />
                  )}
                </div>
              </Flex>
            </SpotlightTarget>
            <Menu
              placement='top-end-bottom-end'
              isOpen={isGraphBtnMenuOpen}
              anchorRef={graphbtnRef}
              onClose={() => setIsGraphBtnMenuOpen(false)}
            >
              <Menu.Items
                htmlAttributes={{
                  id: 'default-menu',
                }}
              >
                <Menu.Item title='Graph Schema' onClick={handleSchemaView} isDisabled={!connectionStatus} />
                <Menu.Item
                  title='Explore Graph'
                  onClick={handleOpenGraphClick}
                  isDisabled={!filesData.some((f) => f?.status === 'Completed')}
                />
                <Menu.Item
                  title='Merge Duplicate Entities'
                  onClick={handleMergeDuplicateEntities}
                  isDisabled={!connectionStatus}
                />
                {/* <Menu.Item
                  title='Create Embeddings'
                  onClick={handleCreateEmbeddings}
                  isDisabled={!connectionStatus || filesData.length === 0}
                /> */}
                <Menu.Item
                  title='Create Entity Embeddings'
                  onClick={handleCreateEntityEmbeddings}
                  isDisabled={!connectionStatus}
                />
              </Menu.Items>
            </Menu>
          </Flex>
        </Flex>
      </div>
    </>
  );
};

export default React.memo(Content);
