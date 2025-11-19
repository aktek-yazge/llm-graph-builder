import { useAuth0 } from '@auth0/auth0-react';
import {
  Checkbox,
  DataGrid,
  DataGridComponents,
  Flex,
  IconButton,
  ProgressBar,
  StatusIndicator,
  TextLink,
  Typography,
  useCopyToClipboard,
  useMediaQuery,
} from '@neo4j-ndl/react';
import {
  ArrowPathIconSolid,
  ClipboardDocumentIconSolid,
  DocumentTextIconSolid,
  ExploreIcon,
  InformationCircleIconOutline,
  XMarkIconOutline,
} from '@neo4j-ndl/react/icons';
import {
  CellContext,
  ColumnFiltersState,
  Row,
  Table,
  createColumnHelper,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { AxiosError } from 'axios';
import React, {
  ForwardRefRenderFunction,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { ThemeWrapperContext } from '../context/ThemeWrapper';
import { useCredentials } from '../context/UserCredentials';
import { useFileContext } from '../context/UsersFiles';
import useServerSideEvent from '../hooks/useSse';
import cancelAPI from '../services/CancelAPI';
import subscribe from '../services/PollingAPI';
import { triggerStatusUpdateAPI } from '../services/ServerSideStatusUpdateAPI';
import { ChildRef, CustomFile, FileTableProps, SourceNode, UserCredentials, statusupdate } from '../types';
import { batchSize, llms } from '../utils/Constants';
import { getQueuedFilesAPI, startChunkingAPI, startGraphCreationAPI } from '../utils/FileAPI';
import { showErrorToast, showNormalToast } from '../utils/Toasts';
import { capitalizeWithUnderscore, statusCheck } from '../utils/Utils';
import { normalizeFileName } from '../utils/utf8';
import BreakDownPopOver from './BreakDownPopOver';
import CustomProgressBar from './UI/CustomProgressBar';
import { IconButtonWithToolTip } from './UI/IconButtonToolTip';

let onlyfortheFirstRender = true;

const FileTable: ForwardRefRenderFunction<ChildRef, FileTableProps> = (props, ref) => {
  const { connectionStatus, setConnectionStatus, onInspect, onRetry, onChunkView, setIsQueueProcessingStopped } = props;
  const { filesData, setFilesData, model, rowSelection, setRowSelection, setSelectedRows, setProcessedCount, queue } =
    useFileContext();
  const { userCredentials, isReadOnlyUser } = useCredentials();
  const columnHelper = createColumnHelper<CustomFile>();
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [filetypeFilter, setFiletypeFilter] = useState<string>('');
  const [fileSourceFilter, setFileSourceFilter] = useState<string>('');
  const [llmtypeFilter, setLLmtypeFilter] = useState<string>('');
  const skipPageResetRef = useRef<boolean>(false);
  const [_, copy] = useCopyToClipboard();
  const { colorMode } = useContext(ThemeWrapperContext);
  const [copyRow, setCopyRow] = useState<boolean>(false);
  const islargeDesktop = useMediaQuery(`(min-width:1440px )`);
  const tableRef = useRef(null);
  const { isAuthenticated } = useAuth0();

  // V2 Queue işlemleri için state
  const [v2SelectedFileIds, setV2SelectedFileIds] = useState<Set<number>>(new Set());
  const [v2ProcessingFileId, setV2ProcessingFileId] = useState<number | null>(null);

  // V2 Reset fonksiyonu - process'te takılan dosyaları pending'e çeker
  // V2 Queue dosyalarını yeniden yükle
  const reloadV2Files = useCallback(async () => {
    try {
      const response = await getQueuedFilesAPI();
      if (response?.status === 'Success' && response?.data?.files) {
        const v2Files = response.data.files.map((file: any) => {
          // V2 Workflow: status belirleme
          // status alanı + chunking_status/graph_status alanlarına bakarak durumu belirle
          let status = 'New';

          // status = "processing" kontrolü (herhangi bir işlem yapılıyor)
          if (file.status === 'processing') {
            if (file.embedding_status === 'processing') {
              status = 'Processing Embeddings'; // Embedding oluşturuluyor
            } else if (file.graph_status === 'processing') {
              status = 'Processing Graph'; // Graph creation yapılıyor
            } else if (file.chunking_status === 'chunking') {
              status = 'Processing Chunks'; // Chunking yapılıyor
            } else if (file.chunking_status === 'extracting') {
              status = 'Extracting'; // Image extraction yapılıyor
            } else {
              status = 'Processing'; // Genel processing
            }
          }
          // status = "queued" kontrolü (kuyrukta bekliyor)
          else if (file.status === 'queued') {
            if (file.chunking_status === 'ready' && file.graph_status === 'pending') {
              status = 'Queued for Chunking'; // Chunking kuyruğunda bekliyor
            } else if (file.chunking_status === 'chunked' && file.graph_status === 'pending') {
              status = 'Queued for Graph'; // Graph creation kuyruğunda bekliyor
            } else if (file.chunking_status === 'pending') {
              status = 'Queued for Extraction'; // Image extraction kuyruğunda bekliyor
            } else {
              status = 'Queued'; // Genel queue
            }
          }
          // status = "completed" kontrolü
          else if (file.status === 'completed' || file.graph_status === 'completed') {
            // Embedding tamamlandıysa bunu göster
            if (file.embedding_status === 'completed') {
              status = 'Completed (with Embeddings)';
            } else {
              status = 'Completed';
            }
          }
          // pending_endorsement kontrolü (endorsement'lar için özel durum)
          else if (file.graph_status === 'pending_endorsement') {
            status = 'Pending Endorsement'; // Endorsement olarak işaretlenmiş, graph creation bekliyor
          }
          // Diğer durumlar (status = "uploaded" veya diğer)
          else if (file.chunking_status === 'failed' || file.graph_status === 'failed' || file.embedding_status === 'failed') {
              status = 'Failed';
          } 
          // Chunked ve embedding tamamlanmış ama graph bekliyor
          else if (file.chunking_status === 'chunked' && file.embedding_status === 'completed' && file.graph_status === 'pending') {
              status = 'Embedded and Ready for Graph';
          }
          // Chunked ama graph bekliyor (embedding yok)
          else if (file.chunking_status === 'chunked' && file.graph_status === 'pending') {
              status = 'Ready for Graph'; // Chunking tamamlandı, graph creation bekliyor
            } else if (file.chunking_status === 'chunked') {
              status = 'Chunked'; // Chunking tamamlandı
            } else if (file.chunking_status === 'ready') {
              status = 'Ready for Chunking'; // Image extraction tamamlandı, chunking'e hazır
            } else if (file.chunking_status === 'pending') {
              status = 'Extracting'; // Image extraction bekliyor (upload sonrası)
            }

          return {
            id: `v2_${file.id}`, // Unique ID for V2 files
            name: file.original_name,
            size: file.file_size || 0, // File size in bytes
            status, // Mapped status
            fileSource: 'V2 Queue',
            sourceUrl: '',
            fileType: file.filename?.split('.').pop()?.toUpperCase() || 'PDF',
            nodesCount: 0,
            relationshipsCount: 0,
            processingProgress:
              // Embedding processing stage
              file.embedding_status === 'processing'
                ? 90
                : file.embedding_status === 'completed'
                  ? 100
                  // Graph processing stage
                  : file.graph_status === 'processing'
                    ? 75
                    : file.graph_status === 'completed'
                      ? 100
                      // Chunking stage  
                      : file.chunking_status === 'chunked'
                        ? 100
                        : file.chunking_status === 'chunking'
                          ? 50
                          : file.chunking_status === 'extracting'
                            ? 25
                            : 0,
            model: file.model_used || 'Not set',
            processingTotalTime: '0',
            chunkNodeCount: 0,
            chunkRelCount: 0,
            // V2 stage info
            v2FileId: file.id,
            upload_status: file.upload_status,
            chunking_status: file.chunking_status,
            graph_status: file.graph_status,
            embedding_status: file.embedding_status,
          };
        });

        // Sadece V2 dosyalarını set et (V1'leri gizle)
        setFilesData(v2Files);
      }
    } catch (error) {
      // Failed silently
    }
  }, [setFilesData]);

  // V2 Reset fonksiyonu - process'te takılan dosyaları pending'e çeker
  const resetV2FileStage = useCallback(async (
    fileId: number,
    fileName: string,
    chunking_status?: string,
    graph_status?: string,
    embedding_status?: string
  ) => {
    try {
      let resetStage = '';

      // Hangi aşamada takılmışsa o aşamayı reset et
      if (chunking_status === 'chunking') {
        resetStage = 'chunking';
      } else if (graph_status === 'processing') {
        resetStage = 'graph';
      } else if (embedding_status === 'processing') {
        resetStage = 'graph'; // Embedding için graph reset yeterli
      } else if (chunking_status === 'failed') {
        resetStage = 'chunking';
      } else if (graph_status === 'failed') {
        resetStage = 'graph';
      } else {
        showErrorToast('Bu dosya process durumunda değil');
        return;
      }

      // Mevcut resetFileStageAPI'yi kullan
      const { resetFileStageAPI } = await import('../utils/FileAPI');
      const response = await resetFileStageAPI(fileId, resetStage as 'upload' | 'chunking' | 'graph');

      if (response.status === 'Success' || response.status === 'success') {
        showNormalToast(`${fileName} ${resetStage} aşaması pending durumuna çekildi`);
        // Dosya listesini yenile
        await reloadV2Files();
      } else {
        showErrorToast(`Reset işlemi başarısız: ${response.message}`);
      }
    } catch (error) {
      showErrorToast('Reset işlemi sırasında hata oluştu');
    }
  }, [reloadV2Files]);



  const { updateStatusForLargeFiles } = useServerSideEvent(
    (inMinutes, time, fileName) => {
      showNormalToast(`${fileName} will take approx ${time} ${inMinutes ? 'Min' : 'Sec'}`);
    },
    (fileName) => {
      showErrorToast(`${fileName} Failed to process`);
    }
  );

  const handleCopy = (rowData: any) => {
    const rowString = JSON.stringify(rowData, null, 2);
    copy(rowString);
    setCopyRow(true);
    setTimeout(() => {
      setCopyRow(false);
    }, 3000);
  };
  const columns = useMemo(
    () => [
      {
        id: 'select',
        header: ({ table }: { table: Table<CustomFile> }) => {
          // V2 dosyaları için daha esnek kontrol: sadece gerçekten işlem yapılan dosyaları disable et
          // V2 dosyaları queue'da bekliyor olabilir ama seçilebilir olmalı
          const processingcheck = table.getRowModel().rows.some((i) => {
            const file = i.original;
            // V2 dosyaları için: sadece gerçekten işlem yapılan dosyaları disable et
            if (file.fileSource === 'V2 Queue') {
              // V2 dosyaları için: "Processing Graph", "Processing Chunks", "Extracting" seçilebilir
              // Sadece "Processing" (genel) veya "Uploading" disable olsun
              return (
                file.status === 'Processing' && !file.status?.includes('Graph') && !file.status?.includes('Chunks')
              );
            }
            // V1 dosyaları için: eski mantık
            return file.status === 'Processing';
          });
          return (
            <Checkbox
              ariaLabel='header-checkbox'
              isChecked={table.getIsAllRowsSelected()}
              onChange={table.getToggleAllRowsSelectedHandler()}
              isDisabled={processingcheck}
              htmlAttributes={{
                title: processingcheck
                  ? `Files are still processing please select individual checkbox for deletion`
                  : 'select all rows for deletion',
              }}
            />
          );
        },
        cell: ({ row }: { row: Row<CustomFile> }) => {
          const isV2File = row.original.fileSource === 'V2 Queue';
          const { v2FileId } = row.original;
          const file = row.original;

          // V2 dosyaları için daha esnek kontrol
          let isDisabled =
            !row.getCanSelect() || row.original.status === 'Uploading' || row.original.status === 'Waiting';

          if (isV2File) {
            // V2 dosyaları için: sadece gerçekten işlem yapılan dosyaları disable et
            // "Processing Graph", "Processing Chunks", "Extracting" seçilebilir
            // Sadece genel "Processing" disable olsun
            if (file.status === 'Processing' && !file.status.includes('Graph') && !file.status.includes('Chunks')) {
              isDisabled = true;
            }
          } else if (file.status === 'Processing') {
            // V1 dosyaları için: eski mantık
            isDisabled = true;
          }

          return (
            <div className='px-1'>
              <Checkbox
                ariaLabel='row-selection'
                isChecked={row.getIsSelected()}
                isDisabled={isDisabled}
                onChange={() => {
                  // React table'ın selection'ını kullan, ayrı state'e gerek yok
                  row.toggleSelected();
                }}
              />
            </div>
          );
        },
        size: 80,
      },
      columnHelper.accessor((row) => row.name, {
        id: 'name',
        cell: (info) => {
          return (
            <div className='textellipsis'>
              <span
                title={
                  (info.row.original?.fileSource === 's3 bucket' && info.row.original?.sourceUrl) ||
                  (info.row.original?.fileSource === 'youtube' && info.row.original?.sourceUrl) ||
                  info.getValue()
                }
              >
                {info.getValue()}
              </span>
            </div>
          );
        },
        header: () => <span>Name</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.status, {
        id: 'status',
        cell: (info) => {
          const statusValue = info.getValue();
          const isProcessingStatus = statusValue === 'Processing' || 
                                     statusValue === 'Extracting' || 
                                     statusValue === 'Processing Chunks' || 
                                     statusValue === 'Processing Graph' ||
                                     statusValue === 'Processing Embeddings';
          
          if (!isProcessingStatus) {
            return (
              <div
                className='cellClass flex! gap-1 items-center'
                title={info.row.original?.status === 'Failed' ? info.row.original?.errorMessage : ''}
              >
                <div>
                  <StatusIndicator type={statusCheck(statusValue)} />
                </div>
                <div>{statusValue}</div>
                {(statusValue === 'Completed' || statusValue === 'Failed' || statusValue === 'Cancelled') &&
                  !isReadOnlyUser && (
                    <span className='mx-1'>
                      <IconButtonWithToolTip
                        placement='right'
                        text='Reset to Graph Creation'
                        size='small'
                        label='Reset to Graph Creation'
                        clean
                        onClick={() => {
                          const isV2File = info.row.original.fileSource === 'V2 Queue';
                          if (isV2File && info.row.original.v2FileId) {
                            resetV2FileStage(
                              info.row.original.v2FileId,
                              info.row.original.name as string,
                              info.row.original.chunking_status,
                              info.row.original.graph_status,
                              info.row.original.embedding_status
                            );
                          } else {
                            onRetry(info?.row?.id as string);
                          }
                        }}
                      >
                        <ArrowPathIconSolid className='n-size-token-4' />
                      </IconButtonWithToolTip>
                    </span>
                  )}
              </div>
            );
          } else if (
            isProcessingStatus &&
            info.row.original.processingProgress === undefined
          ) {
            return (
              <div className='cellClass flex! gap-1 items-center'>
                <div>
                  <StatusIndicator type={statusCheck(statusValue)} />
                </div>
                <div>
                  <i>{statusValue === 'Extracting' ? 'Extracting' : statusValue === 'Processing Chunks' ? 'Processing Chunks' : statusValue === 'Processing Graph' ? 'Processing Graph' : statusValue === 'Processing Embeddings' ? 'Processing Embeddings' : 'Processing'}</i>
                </div>
                <div className='mx-1'>
                  <IconButton
                    size='medium'
                    htmlAttributes={{
                      title: 'cancel the processing job',
                    }}
                    ariaLabel='cancel job button'
                    isClean
                    isDisabled={info.row.original.processingStatus}
                    onClick={() => {
                      cancelHandler(
                        info.row.original.name as string,
                        info.row.original.id as string,
                        info.row.original.fileSource as string
                      );
                    }}
                  >
                    <XMarkIconOutline />
                  </IconButton>
                </div>
              </div>
            );
          } else if (
            isProcessingStatus &&
            info.row.original.processingProgress != undefined &&
            info.row.original.processingProgress < 100
          ) {
            const headingText = statusValue === 'Extracting' ? 'Extracting ' : 
                               statusValue === 'Processing Chunks' ? 'Processing Chunks ' : 
                               statusValue === 'Processing Graph' ? 'Processing Graph ' : 
                               statusValue === 'Processing Embeddings' ? 'Processing Embeddings ' :
                               'Processing ';
            return (
              <div className='cellClass'>
                <ProgressBar
                  heading={headingText}
                  size='small'
                  value={info.row.original.processingProgress}
                ></ProgressBar>
                <div className='mx-1'>
                  <IconButton
                    size='medium'
                    htmlAttributes={{
                      title: 'cancel the processing job',
                    }}
                    ariaLabel='cancel job button'
                    isClean={true}
                    isDisabled={info.row.original.processingStatus}
                    onClick={() => {
                      cancelHandler(
                        info.row.original.name as string,
                        info.row.original.id as string,
                        info.row.original.fileSource as string
                      );
                    }}
                  >
                    <XMarkIconOutline />
                  </IconButton>
                </div>
              </div>
            );
          }
          return (
            <div className='cellClass flex! gap-1'>
              <div>
                <StatusIndicator type={statusCheck(statusValue)} />
              </div>
              <div>{statusValue}</div>
            </div>
          );
        },
        header: () => <span>Status</span>,
        footer: (info) => info.column.id,
        filterFn: 'statusFilter' as any,
        size: 250,
        meta: {
          columnActions: {
            actions: [
              {
                title: (
                  <span className={`${statusFilter === 'All' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    All Files
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('All');
                  table.getColumn('status')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Completed' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='success'></StatusIndicator> Completed Files
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Completed');
                  table.getColumn('status')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'New' ? 'n-bg-palette-primary-bg-selected' : 'p-2'} p-2`}>
                    <StatusIndicator type='info'></StatusIndicator> New Files
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('New');
                  table.getColumn('status')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Failed' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='danger'></StatusIndicator> Failed Files
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Failed');
                  table.getColumn('status')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
            ],
            hasDefaultSortingActions: false,
          },
        },
      }),
      columnHelper.accessor((row) => row.uploadProgress, {
        id: 'uploadprogess',
        cell: (info: CellContext<CustomFile, string>) => {
          if (Number(info.getValue()) === 100 || info.row.original?.status === 'New') {
            return (
              <div className='flex! gap-1 items-center'>
                <Typography variant='body-medium'>
                  <StatusIndicator type='success' />
                </Typography>
                <Typography variant='body-medium'>Uploaded</Typography>
              </div>
            );
          } else if (info.row.original?.status === 'Uploading') {
            return <CustomProgressBar value={Number(info?.getValue())}></CustomProgressBar>;
          } else if (info.row.original?.status === 'Failed') {
            return (
              <div className='flex! gap-1 items-center'>
                <Typography variant='body-medium'>
                  <StatusIndicator type='danger' />
                </Typography>
                <Typography variant='body-medium'> NA</Typography>
              </div>
            );
          }
          return (
            <div className='flex! items-center gap-1'>
              <Typography variant='body-medium'>
                <StatusIndicator type='success' />
              </Typography>
              <Typography variant='body-medium'>Uploaded</Typography>
            </div>
          );
        },
        header: () => <span>Upload Status</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.size, {
        id: 'fileSize',
        cell: (info: CellContext<CustomFile, string>) => <i>{(Number(info?.getValue()) / 1000)?.toFixed(2)}</i>,
        header: () => <span>Size (KB)</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row, {
        id: 'source',
        cell: (info) => {
          if (
            info.row.original.fileSource === 'youtube' ||
            info.row.original.fileSource === 'Wikipedia' ||
            info.row.original.fileSource === 'web-url'
          ) {
            return (
              <Flex>
                <span>
                  <TextLink type='external' target='_blank' href={info.row.original.sourceUrl}>
                    {info.row.original.fileSource}
                  </TextLink>
                </span>
              </Flex>
            );
          }
          return (
            <div>
              <span>{info.row.original.fileSource}</span>
            </div>
          );
        },
        header: () => <span>Source</span>,
        footer: (info) => info.column.id,
        filterFn: 'fileSourceFilter' as any,
        meta: {
          columnActions: {
            actions: [
              {
                title: (
                  <span className={`${fileSourceFilter === 'All' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    All Sources
                  </span>
                ),
                onClick: () => {
                  setFileSourceFilter('All');
                  table.getColumn('source')?.setFilterValue(true);
                },
              },
              ...Array.from(new Set(filesData.map((f) => f.fileSource))).map((t) => {
                return {
                  title: (
                    <span className={`${t === fileSourceFilter ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                      {t}
                    </span>
                  ),
                  onClick: () => {
                    setFileSourceFilter(t as string);
                    table.getColumn('source')?.setFilterValue(true);
                    skipPageResetRef.current = true;
                  },
                };
              }),
            ],
            hasDefaultSortingActions: false,
          },
        },
      }),
      columnHelper.accessor((row) => row, {
        id: 'type',
        cell: (info) => {
          return (
            <div>
              <span>{info.row.original.type}</span>
            </div>
          );
        },
        header: () => <span>Type</span>,
        footer: (info) => info.column.id,
        filterFn: 'fileTypeFilter' as any,
        meta: {
          columnActions: {
            actions: [
              {
                title: (
                  <span className={`${filetypeFilter === 'All' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    All Types
                  </span>
                ),
                onClick: () => {
                  setFiletypeFilter('All');
                  table.getColumn('type')?.setFilterValue(true);
                },
              },
              ...Array.from(new Set(filesData.map((f) => f.type))).map((t) => {
                return {
                  title: (
                    <span className={`${t === filetypeFilter ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>{t}</span>
                  ),
                  onClick: () => {
                    setFiletypeFilter(t as string);
                    table.getColumn('type')?.setFilterValue(true);
                    skipPageResetRef.current = true;
                  },
                };
              }),
            ],
            hasDefaultSortingActions: false,
          },
        },
      }),
      columnHelper.accessor((row) => row.model, {
        id: 'model',
        cell: (info) => {
          const model = info.getValue();
          return <i>{capitalizeWithUnderscore(model)}</i>;
        },
        header: () => <span>Model</span>,
        footer: (info) => info.column.id,
        filterFn: 'llmTypeFilter' as any,
        meta: {
          columnActions: {
            actions: [
              {
                title: (
                  <span className={`${llmtypeFilter === 'All' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    All
                  </span>
                ),
                onClick: () => {
                  setLLmtypeFilter('All');
                  table.getColumn('model')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
              ...llms.map((m) => {
                return {
                  title: (
                    <span className={`${m === llmtypeFilter ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>{m}</span>
                  ),
                  onClick: () => {
                    setLLmtypeFilter(m);
                    table.getColumn('model')?.setFilterValue(true);
                    skipPageResetRef.current = true;
                  },
                };
              }),
            ],
            hasDefaultSortingActions: false,
          },
        },
      }),
      columnHelper.accessor((row) => row.nodesCount, {
        id: 'NodesCount',
        cell: (info) => {
          const hasNodeBreakDownValues =
            info.row.original.chunkNodeCount > 0 ||
            info.row.original.communityNodeCount > 0 ||
            info.row.original.entityNodeCount > 0;

          return (
            <Flex alignItems='center' flexDirection='row'>
              <i>{info.getValue()}</i>
              {hasNodeBreakDownValues &&
                (info.row.original.status === 'Completed' ||
                  info.row.original.status === 'Failed' ||
                  info.row.original.status === 'Cancelled') && (
                  <BreakDownPopOver file={info.row.original} isNodeCount={true} />
                )}
            </Flex>
          );
        },
        header: () => <span>Nodes</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.relationshipsCount, {
        id: 'relationshipCount',
        cell: (info) => {
          const hasRelationsBreakDownValues =
            info.row.original.chunkRelCount > 0 ||
            info.row.original.communityRelCount > 0 ||
            info.row.original.entityEntityRelCount > 0;
          return (
            <Flex alignItems='center' flexDirection='row'>
              <i>{info.getValue()}</i>
              {hasRelationsBreakDownValues &&
                (info.row.original.status === 'Completed' ||
                  info.row.original.status === 'Failed' ||
                  info.row.original.status === 'Cancelled') && (
                  <BreakDownPopOver file={info.row.original} isNodeCount={false} />
                )}
            </Flex>
          );
        },
        header: () => <span>Relations</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.status, {
        id: 'inspect',
        cell: (info) => (
          <>
            <IconButtonWithToolTip
              placement='right'
              text='Graph'
              size='large'
              label='Graph view'
              disabled={info.getValue() === 'New' || info.getValue() === 'Uploading'}
              clean
              onClick={() => onInspect(info?.row?.original?.name as string)}
            >
              <ExploreIcon className='n-size-token-7' />
            </IconButtonWithToolTip>
            <IconButtonWithToolTip
              placement='left'
              text={copyRow ? 'Copied' : 'Copy'}
              size='large'
              label='Copy Row'
              disabled={info.getValue() === 'Uploading'}
              clean
              onClick={() => {
                const copied = { ...info.row.original };
                delete copied.accessToken;
                handleCopy(copied);
              }}
            >
              <ClipboardDocumentIconSolid className={`${copyRow ? 'cursor-progress!' : 'cursor'} `} />
            </IconButtonWithToolTip>
            <IconButtonWithToolTip
              onClick={() => {
                onChunkView(info?.row?.original?.name as string);
              }}
              clean
              placement='left'
              label='chunktextaction'
              text='View Chunks'
              size='large'
              disabled={info.getValue() === 'Uploading' || info.getValue() === 'New'}
            >
              <DocumentTextIconSolid className='n-size-token-7' />
            </IconButtonWithToolTip>
          </>
        ),
        maxSize: 300,
        minSize: 180,
        header: () => <span>Actions</span>,
        footer: (info) => info.column.id,
      }),
    ],
    [
      filesData.length,
      statusFilter,
      filetypeFilter,
      llmtypeFilter,
      fileSourceFilter,
      isReadOnlyUser,
      colorMode,
      isReadOnlyUser,
      colorMode,
      copyRow,
      resetV2FileStage,
    ]
  );

  const table = useReactTable({
    data: filesData, // Artık sadece V2 files var
    columns,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    onColumnFiltersChange: setColumnFilters,
    state: {
      columnFilters,
      rowSelection,
    },
    initialState: {
      pagination: {
        pageSize: 100,
      },
    },
    onRowSelectionChange: setRowSelection,
    filterFns: {
      statusFilter: (row, columnId, filterValue) => {
        if (statusFilter === 'All') {
          return row;
        }
        const value = filterValue ? row.original[columnId] === statusFilter : row.original[columnId];
        return value;
      },
      fileTypeFilter: (row) => {
        if (filetypeFilter === 'All') {
          return true;
        }
        return row.original.type === filetypeFilter;
      },
      fileSourceFilter: (row) => {
        if (fileSourceFilter === 'All') {
          return true;
        }
        return row.original.fileSource === fileSourceFilter;
      },
      llmTypeFilter: (row) => {
        if (llmtypeFilter === 'All') {
          return true;
        }
        return row.original.model === llmtypeFilter;
      },
    },
    enableGlobalFilter: false,
    autoResetPageIndex: skipPageResetRef.current,
    enableRowSelection: true,
    enableMultiRowSelection: true,
    getRowId: (row) => row.id,
    enableSorting: true,
    getSortedRowModel: getSortedRowModel(),
  });

  useEffect(() => {
    skipPageResetRef.current = false;
  }, [filesData.length]);

  const handleFileUploadError = (error: AxiosError) => {
    // @ts-ignore
    const errorfile = decodeURI(error?.config?.url?.split('?')[0].split('/').at(-1));
    setProcessedCount((prev) => Math.max(prev - 1, 0));
    setFilesData((prevfiles) =>
      prevfiles.map((curfile) => (curfile.name === errorfile ? { ...curfile, status: 'Failed' } : curfile))
    );
  };

  const handleSmallFile = (item: SourceNode, userCredentials: UserCredentials) => {
    subscribe(item.fileName, userCredentials, updatestatus, updateProgress).catch(handleFileUploadError);
  };

  const handleLargeFile = (item: SourceNode, userCredentials: UserCredentials) => {
    triggerStatusUpdateAPI(item.fileName, userCredentials, updateStatusForLargeFiles);
  };

    // V1 sources_list endpoint'i devre dışı bırakıldı - V2 Queue sistemi kullanılıyor
    // V2 dosyaları getQueuedFilesAPI ile yükleniyor (reloadV2Files fonksiyonu)
  // useEffect removed - no longer needed

  useEffect(() => {
    if (connectionStatus && filesData.length && onlyfortheFirstRender && !isReadOnlyUser) {
      // V2 dosyaları için daha esnek kontrol: sadece gerçekten işlem yapılan dosyaları say
      const processingFilesCount = filesData.filter((f) => {
        if (f.fileSource === 'V2 Queue') {
          // V2 dosyaları için: sadece genel "Processing" status'ünü say
          // "Processing Graph", "Processing Chunks", "Extracting" sayılmasın
          return f.status === 'Processing' && !f.status.includes('Graph') && !f.status.includes('Chunks');
        }
        return f.status === 'Processing';
      }).length;
      if (processingFilesCount) {
        if (processingFilesCount === 1) {
          setProcessedCount(1);
        }
        showNormalToast(`Files are in processing please wait till previous batch completes`);
      } else {
        const waitingQueue: CustomFile[] = JSON.parse(
          localStorage.getItem('waitingQueue') ?? JSON.stringify({ queue: [] })
        ).queue;

        // Queue processing durumunu kontrol et
        const isQueueProcessingStopped = JSON.parse(localStorage.getItem('isQueueProcessingStopped') ?? 'false');

        if (waitingQueue.length && !isQueueProcessingStopped) {
          props.handleGenerateGraph();
        }
      }
      onlyfortheFirstRender = false;
    }
  }, [connectionStatus, filesData.length, isReadOnlyUser]);

  // V2 Queue dosyalarını ilk render'da yükle
  useEffect(() => {
    reloadV2Files();
  }, [reloadV2Files]);

  // V2 dosyaları için periyodik polling (her 3 saniyede bir)
  useEffect(() => {
    // V2 dosyaları var mı kontrol et
    const hasV2Files = filesData.some((f) => f.fileSource === 'V2 Queue');
    if (!hasV2Files) {
      return;
    }

    // Her 3 saniyede bir durumları güncelle (tüm V2 dosyaları için)
    const interval = setInterval(() => {
      reloadV2Files();
    }, 3000);

    return () => clearInterval(interval);
  }, [filesData, reloadV2Files]);

  const cancelHandler = async (fileName: string, id: string, fileSource: string) => {
    // V2 Queue dosyası mı kontrol et
    const isV2File = fileSource === 'V2 Queue';
    const currentFile = filesData.find((f) => f.id === id);

    if (isV2File && currentFile?.v2FileId) {
      try {
        // V2 dosyası için file-specific cancel API'sini kullan
        const { cancelFileProcessingAPI } = await import('../utils/FileAPI');
        const result = await cancelFileProcessingAPI(currentFile.v2FileId);

        if (result.status === 'Success' || result.status === 'success') {
          showNormalToast(`${fileName} işleme iptal edildi`);
          // Dosya listesini yenile
          await reloadV2Files();
        } else {
          showErrorToast(`İptal işlemi başarısız: ${result.message || 'Bilinmeyen hata'}`);
        }
      } catch (error) {
        showErrorToast('İşlem iptal sırasında hata oluştu');
      }
      return;
    }

    // V1 dosyaları için eski cancel mantığı
    setFilesData((prevfiles) =>
      prevfiles.map((curfile) => {
        if (curfile.id === id) {
          return {
            ...curfile,
            processingStatus: true,
          };
        }
        return curfile;
      })
    );
    try {
      const res = await cancelAPI([fileName], [fileSource]);
      if (res.data.status === 'Success') {
        setFilesData((prevfiles) =>
          prevfiles.map((curfile) => {
            if (curfile.id === id) {
              return {
                ...curfile,
                status: 'Cancelled',
                processingStatus: false,
              };
            }
            return curfile;
          })
        );
        setProcessedCount((prev) => {
          if (prev == batchSize) {
            return batchSize - 1;
          }
          return prev + 1;
        });
        queue.remove((i) => normalizeFileName(i.name) === normalizeFileName(fileName));

        // Queue processing'i durdur
        if (setIsQueueProcessingStopped) {
          setIsQueueProcessingStopped(true);
          showNormalToast('File processing cancelled. Queue processing stopped.');
        } else {
          showNormalToast('File processing cancelled');
        }
      } else {
        let errorobj = { error: res.data.error, message: res.data.message, fileName };
        throw new Error(JSON.stringify(errorobj));
      }
    } catch (err) {
      setFilesData((prevfiles) =>
        prevfiles.map((curfile) => {
          if (curfile.id === id) {
            return {
              ...curfile,
              processingStatus: false,
            };
          }
          return curfile;
        })
      );
      if (err instanceof Error) {
        const error = JSON.parse(err.message);
        if (Object.keys(error).includes('fileName')) {
          const { message } = error;
          showErrorToast(message);
        }
      }
    }
  };

  const updatestatus = (i: statusupdate) => {
    const { file_name } = i;
    const {
      fileName,
      nodeCount = 0,
      relationshipCount = 0,
      processingTime = 0,
      model,
      status,
      processed_chunk = 0,
      total_chunks,
      chunkNodeCount,
      entityNodeCount,
      communityNodeCount,
      chunkRelCount,
      entityEntityRelCount,
      communityRelCount,
    } = file_name;
    if (fileName && total_chunks) {
      setFilesData((prevfiles) =>
        prevfiles.map((curfile) => {
          if (normalizeFileName(curfile.name) === normalizeFileName(fileName)) {
            return {
              ...curfile,
              status: status,
              nodesCount: nodeCount,
              relationshipsCount: relationshipCount,
              model: model,
              processingTotalTime: processingTime?.toFixed(2),
              processingProgress: Math.floor((processed_chunk / total_chunks) * 100),
              chunkNodeCount: chunkNodeCount ?? 0,
              entityNodeCount: entityNodeCount ?? 0,
              communityNodeCount: communityNodeCount ?? 0,
              chunkRelCount: chunkRelCount ?? 0,
              entityEntityRelCount: entityEntityRelCount ?? 0,
              communityRelCount: communityRelCount ?? 0,
            };
          }
          return curfile;
        })
      );
      setProcessedCount((prev) => {
        if (prev == batchSize) {
          return batchSize - 1;
        }
        return prev + 1;
      });
      queue.remove((i) => normalizeFileName(i.name) === normalizeFileName(fileName));
    }
  };

  const updateProgress = (i: statusupdate) => {
    const { file_name } = i;
    const {
      fileName,
      nodeCount = 0,
      relationshipCount = 0,
      status,
      processed_chunk = 0,
      total_chunks,
      chunkNodeCount,
      entityNodeCount,
      communityNodeCount,
      chunkRelCount,
      entityEntityRelCount,
      communityRelCount,
    } = file_name;
    if (fileName && total_chunks) {
      setFilesData((prevfiles) =>
        prevfiles.map((curfile) => {
          if (normalizeFileName(curfile.name) === normalizeFileName(fileName)) {
            return {
              ...curfile,
              status: status,
              nodesCount: nodeCount,
              relationshipsCount: relationshipCount,
              processingProgress: Math.floor((processed_chunk / total_chunks) * 100),
              chunkNodeCount: chunkNodeCount ?? 0,
              entityNodeCount: entityNodeCount ?? 0,
              communityNodeCount: communityNodeCount ?? 0,
              chunkRelCount: chunkRelCount ?? 0,
              entityEntityRelCount: entityEntityRelCount ?? 0,
              communityRelCount: communityRelCount ?? 0,
            };
          }
          return curfile;
        })
      );
    }
  };

  useImperativeHandle(
    ref,
    () => ({
      getSelectedRows: () => table.getSelectedRowModel().rows.map((r) => r.original),
      getV2SelectedFileIds: () => {
        // React table selection'dan doğrudan al
        return table
          .getSelectedRowModel()
          .rows.map((r) => r.original)
          .filter((f: CustomFile) => f.fileSource === 'V2 Queue' && f.v2FileId)
          .map((f: CustomFile) => f.v2FileId as number);
      },
      getV2SelectedFiles: () => {
        // React table selection'dan doğrudan al (row model'den)
        const selectedRows = table.getSelectedRowModel().rows;
        const result = selectedRows
          .map((r) => r.original)
          .filter((f: CustomFile) => f.fileSource === 'V2 Queue') as CustomFile[];
        return result;
      },
      handleStartChunking: async () => {
        // React table selection'dan al
        const selected = table
          .getSelectedRowModel()
          .rows.map((r) => r.original)
          .filter((f: CustomFile) => f.fileSource === 'V2 Queue' && f.v2FileId)
          .map((f: CustomFile) => f.v2FileId as number);

        if (selected.length === 0) {
          showErrorToast('Please select V2 files first');
          return;
        }

        // Check if all V2 files are selected
        const allV2Files = filesData.filter((f) => f.fileSource === 'V2 Queue' && f.v2FileId);
        const isAllSelected = allV2Files.length > 0 && selected.length === allV2Files.length;

        try {
          if (isAllSelected) {
            // Use "all" parameter
            const response = await startChunkingAPI('all');
            if (response.status === 'Success' || response.status === 'success' || response.data?.status === 'success') {
              const processedCount = response.data?.processed_count || selected.length;
              showNormalToast(`✓ Chunking started for ${processedCount} file(s)`);
            } else {
              showErrorToast(`Failed to start chunking: ${response.message || 'Unknown error'}`);
            }
          } else {
            // Birden fazla dosya seçilmişse, "all" parametresi kullan (backend batch batch işleyecek)
            // Backend zaten batch batch işlemek için dosyaları işaretliyor
            showNormalToast(`${selected.length} dosya için chunking başlatılıyor (batch batch işlenecek)...`);
            const response = await startChunkingAPI('all');
            if (response.status === 'Success' || response.status === 'success' || response.data?.status === 'success') {
              const processedCount = response.data?.processed_count || selected.length;
              showNormalToast(`✓ Chunking started for ${processedCount} file(s) (batch batch işlenecek)`);
            } else {
              showErrorToast(`Failed to start chunking: ${response.message || 'Unknown error'}`);
            }
          }
          setV2SelectedFileIds(new Set());
          await reloadV2Files();
        } catch (error) {
          showErrorToast('Failed to start chunking');
        }
      },
      handleCreateGraph: async () => {
        // React table selection'dan al
        const selectedV2Files = table
          .getSelectedRowModel()
          .rows.map((r) => r.original)
          .filter((f: CustomFile) => f.fileSource === 'V2 Queue' && f.v2FileId) as CustomFile[];

        if (selectedV2Files.length === 0) {
          showErrorToast('Please select V2 files first');
          return;
        }

        // Check if all V2 files are selected
        const allV2Files = filesData.filter((f) => f.fileSource === 'V2 Queue' && f.v2FileId);
        const isAllSelected = allV2Files.length > 0 && selectedV2Files.length === allV2Files.length;

        try {
          if (isAllSelected) {
            // Use "all" parameter - backend will filter files by status
            showNormalToast('Tüm hazır dosyalar için graph oluşturuluyor...');
            const response = await startGraphCreationAPI('all', 'openai_gpt_4o_mini', false);
            if (response.status === 'Success' || response.status === 'success' || response.data?.status === 'success') {
              const processedCount = response.data?.processed_count || 0;
              showNormalToast(`✓ Graph creation started for ${processedCount} file(s)`);
            } else {
              showErrorToast(`Failed to start graph creation: ${response.message || 'Unknown error'}`);
            }
          } else {
            // Chunked olanları filtrele
            const chunkedFiles = selectedV2Files.filter(
              (f) => f.chunking_status === 'chunked' && f.graph_status !== 'completed'
            );
            const notChunkedFiles = selectedV2Files.filter(
              (f) => f.chunking_status !== 'chunked' || f.graph_status === 'completed'
            );

            if (chunkedFiles.length === 0) {
              showErrorToast('Seçilen dosyaların hiçbiri graph oluşturmaya hazır değildir.');
              return;
            }

            // Uyar varsa göster
            if (notChunkedFiles.length > 0) {
              showNormalToast(
                `⚠️ ${notChunkedFiles.length} dosya henüz hazır değil, sadece ${chunkedFiles.length} dosya için graph oluşturulacak`
              );
            }

            // Dosyaların durumunu Processing olarak güncelle (UI'de göstermek için)
            setFilesData((prev) =>
              prev.map((f) => {
                if (chunkedFiles.some((cf) => cf.v2FileId === f.v2FileId)) {
                  return { ...f, status: 'Processing', processingProgress: 50 };
                }
                return f;
              })
            );

            // Birden fazla dosya seçilmişse, "all" parametresi kullan (backend batch batch işleyecek)
            // Backend zaten batch batch işlemek için dosyaları işaretliyor
            showNormalToast(`${chunkedFiles.length} dosya için graph oluşturuluyor (batch batch işlenecek)...`);
            const response = await startGraphCreationAPI('all', 'openai_gpt_4o_mini', false);
            if (response.status === 'Success' || response.status === 'success' || response.data?.status === 'success') {
              const processedCount = response.data?.processed_count || chunkedFiles.length;
              showNormalToast(`✓ Graph creation started for ${processedCount} file(s) (batch batch işlenecek)`);
            } else {
              showErrorToast(`Failed to start graph creation: ${response.message || 'Unknown error'}`);
            }
          }
          setV2SelectedFileIds(new Set());

          // İlk refresh - processing status'ünü görmek için
          await new Promise((resolve) => setTimeout(resolve, 200));
          reloadV2Files();
          
          // İkinci refresh - emin olmak için
          await new Promise((resolve) => setTimeout(resolve, 500));
          reloadV2Files();
        } catch (error) {
          showErrorToast('Failed to start graph creation');
        }
      },
      reloadV2Files: () => reloadV2Files(),
    }),
    [table, v2SelectedFileIds, reloadV2Files]
  );

  useEffect(() => {
    setSelectedRows(table.getSelectedRowModel().rows.map((i) => i.id));
  }, [table.getSelectedRowModel()]);

  return (
    <>
      {filesData ? (
        <>
          {/* V2 Queue işlemleri artık Content.tsx'teki bottom buttonlarla yapılıyor */}
          <DataGrid
            ref={tableRef}
            isResizable={true}
            tableInstance={table}
            styling={{
              borderStyle: 'all-sides',
              hasZebraStriping: true,
              headerStyle: 'clean',
            }}
            isLoading={isLoading}
            rootProps={{
              className: `absolute h-[67%] left-10 filetable ${!islargeDesktop ? 'top-[17%]' : 'top-[14%]'}`,
              style: { zIndex: 10 },
            }}
            components={{
              Body: () => (
                <DataGridComponents.Body
                  innerProps={{
                    className: colorMode == 'dark' ? 'tbody-dark' : 'tbody-light',
                  }}
                />
              ),
              TableResults: () => {
                if (connectionStatus && !isAuthenticated && !isLoading && filesData.length === 0) {
                  return (
                    <DataGridComponents.TableResults>
                      <Flex flexDirection='row' gap='0' alignItems='center'>
                        <span>
                          <InformationCircleIconOutline className='n-size-token-6' />
                        </span>
                        {` It seems like you haven't ingested any data yet. To begin building your knowledge graph, you'll need to log
            in to the main application.`}
                        <span></span>
                      </Flex>
                    </DataGridComponents.TableResults>
                  );
                } else if (connectionStatus) {
                  return (
                    <DataGridComponents.TableResults>
                      <Flex flexDirection='row' gap='0' alignItems='center'>
                        <span>
                          <InformationCircleIconOutline className='n-size-token-6' />
                        </span>
                        {`Large files may be partially processed up to 10K characters due to resource limit.`}
                        <span></span>
                      </Flex>
                    </DataGridComponents.TableResults>
                  );
                }
                return <DataGridComponents.TableResults></DataGridComponents.TableResults>;
              },
              PaginationNumericButton: ({ isSelected, innerProps, ...restProps }) => {
                return (
                  <DataGridComponents.PaginationNumericButton
                    {...restProps}
                    isSelected={isSelected}
                    innerProps={{
                      ...innerProps,
                      style: {
                        ...(isSelected && {
                          backgroundSize: '200% auto',
                          borderRadius: '10px',
                        }),
                      },
                    }}
                  />
                );
              },
            }}
            isKeyboardNavigable={false}
          />
        </>
      ) : null}
    </>
  );
};

export default React.memo(forwardRef(FileTable));
