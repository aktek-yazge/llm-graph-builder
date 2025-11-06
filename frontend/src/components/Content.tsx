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
import { ChevronDownIconOutline, ChevronUpIconOutline } from '@neo4j-ndl/react/icons';
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
import { extractAPI, getFileStatusAPI, resetFileStageAPI, startChunkingAPI } from '../utils/FileAPI';
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
  const { setUserCredentials, userCredentials, setConnectionStatus, isGdsActive, isReadOnlyUser, isGCSActive } =
    useCredentials();
  const [retryFile, setRetryFile] = useState<string>('');
  const [retryLoading, setRetryLoading] = useState<boolean>(false);
  const [showRetryPopup, toggleRetryPopup] = useReducer((state) => !state, false);
  const [showChunkPopup, toggleChunkPopup] = useReducer((state) => !state, false);
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
    completed: number;
  }>({
    pendingChunking: 0,
    readyForGraph: 0,
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
  useEffect(() => {
    // filesData'dan seçili V2 dosyalarını al
    const selectedV2FileIds = new Set<number>();

    // FileTable'daki checkbox state'ini oku
    const v2Files = childRef.current?.getV2SelectedFiles?.() || [];
    v2Files.forEach((f: CustomFile) => {
      if (f.v2FileId) {
        selectedV2FileIds.add(f.v2FileId);
      }
    });

    if (selectedV2FileIds.size === 0) {
      setV2FilesCategorized({ pendingChunking: 0, readyForGraph: 0, completed: 0 });
      setV2SelectedFileCount(0);
      return;
    }

    // filesData'dan seçili V2 dosyalarını al ve kategorize et
    const selectedFiles = filesData.filter(
      (f: CustomFile) => f.fileSource === 'V2 Queue' && f.v2FileId && selectedV2FileIds.has(f.v2FileId)
    );

    const totalCount = selectedFiles.length;

    const pendingChunking = selectedFiles.filter(
      (f: CustomFile) => f.upload_status === 'uploaded' && f.chunking_status === 'pending'
    ).length;

    const readyForGraph = selectedFiles.filter((f: CustomFile) => f.chunking_status === 'chunked').length;

    const completed = selectedFiles.filter((f: CustomFile) => f.graph_status === 'completed').length;

    setV2SelectedFileCount(totalCount);
    setV2FilesCategorized({ pendingChunking, readyForGraph, completed });
  }, [filesData]);

  const handleDropdownChange = (selectedOption: OptionType | null | void) => {
    if (selectedOption?.value) {
      setModel(selectedOption?.value);
    }
    setFilesData((prevfiles) => {
      return prevfiles.map((curfile) => {
        return {
          ...curfile,
          model:
            curfile.status === 'New' || curfile.status === 'Ready to Reprocess'
              ? (selectedOption?.value ?? '')
              : curfile.model,
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

  const selectedfileslength = useMemo(
    () => childRef.current?.getSelectedRows().length,
    [childRef.current?.getSelectedRows()]
  );

  const newFilecheck = useMemo(
    () =>
      childRef.current?.getSelectedRows().filter((f) => f.status === 'New' || f.status == 'Ready to Reprocess').length,
    [childRef.current?.getSelectedRows()]
  );

  const completedfileNo = useMemo(
    () => childRef.current?.getSelectedRows().filter((f) => f.status === 'Completed').length,
    [childRef.current?.getSelectedRows()]
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

  const handleDeleteFiles = async (deleteEntities: boolean) => {
    try {
      setIsDeleteLoading(true);
      const response = await deleteAPI(childRef.current?.getSelectedRows() as CustomFile[], deleteEntities);
      queue.clear();
      setProcessedCount(0);
      setRowSelection({});
      setIsDeleteLoading(false);
      if (response.data.status == 'Success') {
        showSuccessToast(response.data.message);
        const filenames = childRef.current?.getSelectedRows().map((str) => str.name);
        if (filenames?.length) {
          for (let index = 0; index < filenames.length; index++) {
            const name = filenames[index];
            setFilesData((prev) => prev.filter((f) => f.name != name));
          }
        }
      } else {
        let errorobj = { error: response.data.error, message: response.data.message };
        throw new Error(JSON.stringify(errorobj));
      }
      setShowDeletePopUp(false);
    } catch (err) {
      setIsDeleteLoading(false);
      if (err instanceof Error) {
        const error = JSON.parse(err.message);
        const { message } = error;
        showErrorToast(message);
      }
    }
    setShowDeletePopUp(false);
  };

  // V2 dosyaları durumlarına göre kategorize et
  const getV2FilesCategorized = () => {
    // Seçili dosyaları al
    const v2Files = childRef.current?.getV2SelectedFiles?.() || [];

    const pendingChunking = v2Files.filter(
      (f: CustomFile) => f.upload_status === 'uploaded' && f.chunking_status === 'pending'
    ).length;

    const readyForGraph = v2Files.filter((f: CustomFile) => f.chunking_status === 'chunked').length;

    const completed = v2Files.filter((f: CustomFile) => f.graph_status === 'completed').length;

    return { pendingChunking, readyForGraph, completed };
  };

  // V2 Chunking başlatma handler'ı
  const handleStartChunkingForV2 = async () => {
    const v2Files = childRef.current?.getV2SelectedFiles?.() || [];
    const v2FileIds = childRef.current?.getV2SelectedFileIds();

    if (!v2FileIds || v2FileIds.length === 0) {
      showErrorToast('Hiçbir V2 dosyası seçilmedi');
      return;
    }

    try {
      setIsExtractLoading(true);
      showNormalToast(`${v2FileIds.length} dosya için chunking başlatılıyor...`);

      // Chunking başlat
      for (const fileId of v2FileIds) {
        try {
          const response = await startChunkingAPI(fileId);
          if (response.status === 'Success' || response.status === 'success' || response.data?.status === 'success') {
            showSuccessToast(`Dosya ${fileId} chunking'e alındı`);

            // Bu dosyanın durumunu polling ile kontrol et
            const pollStatus = async () => {
              let attempts = 0;
              const maxAttempts = 600;

              while (attempts < maxAttempts) {
                await new Promise((resolve) => setTimeout(resolve, 5000));

                try {
                  const statusResponse = await getFileStatusAPI(fileId);
                  const fileStatus = statusResponse?.data?.chunking_status;
                  const elapsedSeconds = attempts * 5;

                  if (fileStatus === 'chunked') {
                    showSuccessToast(`✓ Dosya ${fileId} chunking tamamlandı`);
                    childRef.current?.reloadV2Files?.();
                    break;
                  }
                  if (fileStatus === 'failed') {
                    showErrorToast(`✗ Dosya ${fileId} chunking başarısız`);
                    break;
                  }
                  if (elapsedSeconds > 0 && elapsedSeconds % 30 === 0) {
                    showNormalToast(`⏱ Dosya işleniyor... (${Math.floor(elapsedSeconds / 60)} dk)`);
                  }
                } catch (err) {
                  // Continue polling
                }
                attempts++;
              }
            };

            // Polling'i background'da çalıştır
            pollStatus();
          } else {
            showErrorToast(`Dosya ${fileId} chunking başlatılamadı: ${response.message || 'Bilinmeyen hata'}`);
          }
        } catch (error: any) {
          const errorMsg = error.response?.data?.message || error.message || 'Chunking hatası';
          showErrorToast(`Dosya ${fileId}: ${errorMsg}`);
        }
      }
    } catch (error: any) {
      showErrorToast('Chunking işlemi başlatılamadı');
    } finally {
      setIsExtractLoading(false);
    }
  };

  // V2 Chunking reset handler'ı
  const handleResetChunkingForV2 = async () => {
    const v2FileIds = childRef.current?.getV2SelectedFileIds();

    if (!v2FileIds || v2FileIds.length === 0) {
      showErrorToast('Hiçbir V2 dosyası seçilmedi');
      return;
    }

    try {
      setIsExtractLoading(true);
      showNormalToast(`${v2FileIds.length} dosya için chunking reset ediliyor...`);

      for (const fileId of v2FileIds) {
        try {
          const response = await resetFileStageAPI(fileId, 'chunking');
          if (response.status === 'Success' || response.status === 'success') {
            showSuccessToast(`Dosya ${fileId} reset edildi`);
          } else {
            showErrorToast(`Dosya ${fileId} reset başarısız: ${response.message || 'Bilinmeyen hata'}`);
          }
        } catch (error: any) {
          const errorMsg = error.response?.data?.message || error.message || 'Reset hatası';
          showErrorToast(`Dosya ${fileId}: ${errorMsg}`);
        }
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
            />
            <Checkbox
              label='Upload sırasında embedding oluştur'
              isChecked={generateEmbedding}
              onChange={(e) => setGenerateEmbedding(e.target.checked)}
            />
          </div>
          <Flex flexDirection='row' gap='4' className='self-end mb-2.5' flexWrap='wrap'>
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
                isDisabled={isReadOnlyUser || extractLoading}
                className='px-0! flex! items-center justify-between gap-4 chunkingbtn'
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
                disabled={isReadOnlyUser}
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
                <Menu.Item
                  title='Create Embeddings'
                  onClick={handleCreateEmbeddings}
                  isDisabled={!connectionStatus || filesData.length === 0}
                />
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
