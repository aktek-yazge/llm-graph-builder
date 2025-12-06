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
import { batchSize } from '../utils/Constants';
import { getFileDetailsByIdsAPI, getQueuedFilesAPI, startChunkingAPI, startGraphCreationAPI } from '../utils/FileAPI';
import { showErrorToast, showNormalToast } from '../utils/Toasts';
import { statusCheck, url } from '../utils/Utils';
import { normalizeFileName } from '../utils/utf8';
import BreakDownPopOver from './BreakDownPopOver';
import CustomProgressBar from './UI/CustomProgressBar';
import { IconButtonWithToolTip } from './UI/IconButtonToolTip';

let onlyfortheFirstRender = true;

const FileTable: ForwardRefRenderFunction<ChildRef, FileTableProps> = (props, ref) => {
  const { connectionStatus, setConnectionStatus, onInspect, onRetry, onChunkView, setIsQueueProcessingStopped, nameFilter, setNameFilter } = props;
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
  
  // Pagination state for API detail loading
  const [currentPageIndex, setCurrentPageIndex] = useState<number>(0);
  const PAGE_SIZE = 100;
  
  // Detail cache - yüklenen detayları sakla, list API tarafından ezilmesin
  const detailCacheRef = useRef<Map<number, any>>(new Map());

  // V2 Reset fonksiyonu - process'te takılan dosyaları pending'e çeker
  // V2 Queue dosyalarını yeniden yükle
  const reloadV2Files = useCallback(async (pageIndex: number = currentPageIndex) => {
    try {
      const detailOffset = pageIndex * PAGE_SIZE;
      console.log(`📄 FileTable: Fetching files page=${pageIndex}, offset=${detailOffset}, limit=${PAGE_SIZE}`);
      const response = await getQueuedFilesAPI(PAGE_SIZE, detailOffset);
      if (response?.status === 'Success' && response?.data?.files) {
        // API'den gelen detayları cache'e ekle
        response.data.files.forEach((file: any) => {
          if (file._detail === true) {
            detailCacheRef.current.set(file.id, file);
          }
        });
        
        const v2Files = response.data.files.map((file: any) => {
          // Cache'de detay varsa kullan (filter sonrası yüklenen detaylar için)
          // ÖNEMLİ: API'den gelen status alanları (file) cache'deki eski değerleri (cachedDetail) ezmeli!
          const cachedDetail = detailCacheRef.current.get(file.id);
          // Önce cache (detay bilgileri: name, size vs.), sonra API (güncel status'ler)
          const f = cachedDetail 
            ? { ...cachedDetail, ...file, _detail: true }  // API status'leri öncelikli!
            : file;
          
          // V2 Workflow: status belirleme
          // status alanı + chunking_status/graph_status alanlarına bakarak durumu belirle
          let status = 'New';

          // status = "processing" kontrolü (herhangi bir işlem yapılıyor)
          if (f.status === 'processing') {
            if (f.embedding_status === 'processing') {
              status = 'Processing Embeddings';
            } else if (f.graph_status === 'processing') {
              status = 'Processing Graph';
            } else if (f.chunking_status === 'chunking') {
              status = 'Processing Chunks';
            } else if (f.chunking_status === 'extracting') {
              status = 'Extracting';
            } else {
              status = 'Processing';
            }
          }
          // status = "queued" kontrolü
          else if (f.status === 'queued') {
            if (f.chunking_status === 'ready' && f.graph_status === 'pending') {
              status = 'Queued for Chunking';
            } else if (f.chunking_status === 'chunked' && f.graph_status === 'pending') {
              status = 'Queued for Graph';
            } else if (f.chunking_status === 'pending') {
              status = 'Queued for Extraction';
            } else {
              status = 'Queued';
            }
          }
          // status = "completed" kontrolü
          else if (f.status === 'completed' || f.graph_status === 'completed') {
            if (f.embedding_status === 'completed') {
              status = 'Completed (with Embeddings)';
            } else if (f.embedding_status === 'failed') {
              status = 'Completed (Failed Embedding)';
            } else {
              status = 'Completed';
            }
          }
          // pending_endorsement kontrolü
          else if (f.graph_status === 'pending_endorsement') {
            status = 'Pending Endorsement';
          }
          // Diğer durumlar
          else if (f.chunking_status === 'failed' || f.graph_status === 'failed' || f.embedding_status === 'failed') {
            status = 'Failed';
          } 
          else if (f.chunking_status === 'chunked' && f.embedding_status === 'completed' && f.graph_status === 'pending') {
            status = 'Embedded and Ready for Graph';
          }
          else if (f.chunking_status === 'chunked' && f.graph_status === 'pending') {
            status = 'Ready for Graph';
          } else if (f.chunking_status === 'chunked') {
            status = 'Chunked';
          } else if (f.chunking_status === 'ready') {
            status = 'Ready for Chunking';
          } else if (f.chunking_status === 'pending') {
            status = 'Extracting';
          }

          return {
            id: `v2_${f.id}`,
            name: f.original_name || (f._detail === false ? '' : `File #${f.id}`),
            size: f.file_size || 0,
            status,
            fileSource: 'V2 Queue',
            sourceUrl: f.original_name ? `${url()}/files/${encodeURIComponent(f.original_name)}?inline=true` : '',
            fileType: f.filename?.split('.').pop()?.toUpperCase() || 'PDF',
            nodesCount: 0,
            relationshipsCount: 0,
            processingProgress:
              f.embedding_status === 'processing' ? 90
              : f.embedding_status === 'completed' ? 100
              : f.graph_status === 'processing' ? 75
              : f.graph_status === 'completed' ? 100
              : f.chunking_status === 'chunked' ? 100
              : f.chunking_status === 'chunking' ? 50
              : f.chunking_status === 'extracting' ? 25
              : 0,
            model: f.model_used || 'Not set',
            processingTotalTime: '0',
            chunkNodeCount: 0,
            chunkRelCount: 0,
            v2FileId: f.id,
            upload_status: f.upload_status,
            chunking_status: f.chunking_status,
            graph_status: f.graph_status,
            embedding_status: f.embedding_status,
            _detail: f._detail === true,
            // Status completion timestamps
            chunking_completed_at: f.chunking_completed_at,
            graph_completed_at: f.graph_completed_at,
            embedding_completed_at: f.embedding_completed_at,
          };
        });

        // Sadece V2 dosyalarını set et (V1'leri gizle)
        setFilesData(v2Files);
      }
    } catch (error) {
      // Failed silently
    }
  }, [setFilesData, currentPageIndex]);

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

      console.log(`🔄 Resetting processing for file ${fileId} (stage: invalidate)`);

      // Mevcut resetFileStageAPI'yi kullan
      const { resetFileStageAPI } = await import('../utils/FileAPI');
      const response = await resetFileStageAPI(fileId, 'invalidate');

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
          // Filtrelenmiş satırları al (pagination dahil tüm filtrelenmiş satırlar)
          const filteredRows = table.getFilteredRowModel().rows;
          
          // V2 dosyaları için daha esnek kontrol: sadece gerçekten işlem yapılan dosyaları disable et
          const processingcheck = filteredRows.some((i) => {
            const file = i.original;
            if (file.fileSource === 'V2 Queue') {
              return (
                file.status === 'Processing' && !file.status?.includes('Graph') && !file.status?.includes('Chunks')
              );
            }
            return file.status === 'Processing';
          });
          
          // Filtrelenmiş satırların hepsi seçili mi kontrol et
          const selectableRows = filteredRows.filter(row => row.getCanSelect());
          const allFilteredSelected = selectableRows.length > 0 && 
            selectableRows.every(row => row.getIsSelected());
          
          // Filtrelenmiş satırlar için toggle handler
          const handleToggleAllFiltered = () => {
            const shouldSelect = !allFilteredSelected;
            selectableRows.forEach(row => {
              row.toggleSelected(shouldSelect);
            });
          };
          
          return (
            <Checkbox
              ariaLabel='header-checkbox'
              isChecked={allFilteredSelected}
              onChange={handleToggleAllFiltered}
              isDisabled={processingcheck}
              htmlAttributes={{
                title: processingcheck
                  ? `Files are still processing please select individual checkbox for deletion`
                  : `Select all ${selectableRows.length} filtered rows`,
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
          const fileSource = info.row.original?.fileSource;
          const sourceUrl = info.row.original?.sourceUrl;
          const fileName = info.getValue();
          
          // V2 Queue dosyaları için link oluştur
          if (fileSource === 'V2 Queue' && sourceUrl) {
            return (
              <div className='textellipsis' title={fileName}>
                <TextLink
                  type='external'
                  target='_blank'
                  href={sourceUrl}
                >
                  {fileName}
                </TextLink>
              </div>
            );
          }
          
          return (
            <div className='textellipsis'>
              <span
                title={
                  (fileSource === 's3 bucket' && sourceUrl) ||
                  (fileSource === 'youtube' && sourceUrl) ||
                  fileName
                }
              >
                {fileName}
              </span>
            </div>
          );
        },
        header: () => <span>Name</span>,
        footer: (info) => info.column.id,
        filterFn: 'nameFilter' as any,
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
                  table.getColumn('status')?.setFilterValue('All');
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Completed' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='success'></StatusIndicator> Completed
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Completed');
                  table.getColumn('status')?.setFilterValue('Completed');
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Chunked' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='success'></StatusIndicator> Chunked
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Chunked');
                  table.getColumn('status')?.setFilterValue('Chunked');
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Processing' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='warning'></StatusIndicator> Processing
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Processing');
                  table.getColumn('status')?.setFilterValue('Processing');
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Chunking' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='warning'></StatusIndicator> Chunking
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Chunking');
                  table.getColumn('status')?.setFilterValue('Chunking');
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'ReadyChunking' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='info'></StatusIndicator> Ready for Chunking
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('ReadyChunking');
                  table.getColumn('status')?.setFilterValue('ReadyChunking');
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'ReadyGraph' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='success'></StatusIndicator> Ready for Graph
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('ReadyGraph');
                  table.getColumn('status')?.setFilterValue('ReadyGraph');
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Queued' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='info'></StatusIndicator> Queued
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Queued');
                  table.getColumn('status')?.setFilterValue('Queued');
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'New' ? 'n-bg-palette-primary-bg-selected' : 'p-2'} p-2`}>
                    <StatusIndicator type='info'></StatusIndicator> New
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('New');
                  table.getColumn('status')?.setFilterValue('New');
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Failed' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='danger'></StatusIndicator> Failed
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Failed');
                  table.getColumn('status')?.setFilterValue('Failed');
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
      columnHelper.accessor((row) => row.chunking_completed_at, {
        id: 'chunkingCompletedAt',
        cell: (info) => {
          const completedAt = info.getValue();
          if (!completedAt) return <span className='text-gray-400'>-</span>;
          const date = new Date(completedAt);
          return (
            <span title={date.toLocaleString('tr-TR')} className='text-xs'>
              {date.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit' })}
              {' '}
              {date.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })}
            </span>
          );
        },
        header: () => <span>Chunked</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.graph_completed_at, {
        id: 'graphCompletedAt',
        cell: (info) => {
          const completedAt = info.getValue();
          if (!completedAt) return <span className='text-gray-400'>-</span>;
          const date = new Date(completedAt);
          return (
            <span title={date.toLocaleString('tr-TR')} className='text-xs'>
              {date.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit' })}
              {' '}
              {date.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })}
            </span>
          );
        },
        header: () => <span>Graph</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.embedding_completed_at, {
        id: 'embeddingCompletedAt',
        cell: (info) => {
          const completedAt = info.getValue();
          if (!completedAt) return <span className='text-gray-400'>-</span>;
          const date = new Date(completedAt);
          return (
            <span title={date.toLocaleString('tr-TR')} className='text-xs'>
              {date.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit' })}
              {' '}
              {date.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })}
            </span>
          );
        },
        header: () => <span>Embed</span>,
        footer: (info) => info.column.id,
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
      nameFilter,
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
      pagination: {
        pageIndex: currentPageIndex,
        pageSize: PAGE_SIZE,
      },
    },
    onPaginationChange: (updater) => {
      // React Table pagination state updater
      const newState = typeof updater === 'function' 
        ? updater({ pageIndex: currentPageIndex, pageSize: PAGE_SIZE })
        : updater;
      
      if (newState.pageIndex !== currentPageIndex) {
        console.log(`📄 Page changed: ${currentPageIndex} -> ${newState.pageIndex}`);
        setCurrentPageIndex(newState.pageIndex);
        // Yeni sayfa için detayları yükle
        reloadV2Files(newState.pageIndex);
      }
    },
    manualPagination: false, // Client-side pagination (tüm veri yüklenmiş)
    onRowSelectionChange: setRowSelection,
    filterFns: {
      nameFilter: (row, columnId, filterValue) => {
        if (!filterValue || filterValue === '') {
          return true;
        }
        const name = (row.original.name || '') as string;
        return name.toLowerCase().includes(filterValue.toLowerCase());
      },
      statusFilter: (row, columnId, filterValue) => {
        // filterValue artık filter adını içeriyor (örn: 'Completed', 'Chunked', vb.)
        const currentFilter = filterValue || statusFilter;
        
        if (!currentFilter || currentFilter === 'All') {
          return true;
        }
        const status = row.original[columnId] as string;
        
        // Exact match for specific statuses
        if (currentFilter === 'Completed') {
          return status === 'Completed' || status.includes('Completed');
        }
        if (currentFilter === 'Chunked') {
          return status === 'Chunked' || status === 'Ready for Graph' || status === 'Embedded and Ready for Graph';
        }
        if (currentFilter === 'Processing') {
          return status === 'Processing' || status === 'Processing Chunks' || 
                 status === 'Processing Graph' || status === 'Processing Embeddings' || 
                 status === 'Extracting';
        }
        if (currentFilter === 'Chunking') {
          return status === 'Processing Chunks' || status === 'Chunking';
        }
        if (currentFilter === 'ReadyChunking') {
          return status === 'Ready for Chunking';
        }
        if (currentFilter === 'ReadyGraph') {
          return status === 'Ready for Graph' || status === 'Embedded and Ready for Graph' || 
                 status === 'Chunked';
        }
        if (currentFilter === 'Queued') {
          return status === 'Queued' || status === 'Queued for Chunking' || 
                 status === 'Queued for Graph' || status === 'Queued for Extraction';
        }
        if (currentFilter === 'New') {
          return status === 'New';
        }
        if (currentFilter === 'Failed') {
          return status === 'Failed';
        }
        
        return status === currentFilter;
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

  // filesData güncellendiğinde, filtreye uymayan seçimleri temizle
  // Örn: Processing filtresinde 4 dosya seçili, 1'i Completed olunca 3 kalmalı
  useEffect(() => {
    if (!statusFilter || statusFilter === 'All' || statusFilter === '') {
      return; // Filter yoksa veya "All" ise bir şey yapma
    }
    
    // Mevcut seçili satırları al
    const selectedRows = table.getSelectedRowModel().rows;
    if (selectedRows.length === 0) return;
    
    // Filtrelenmiş satırların ID'lerini al
    const filteredRowIds = new Set(table.getFilteredRowModel().rows.map(r => r.id));
    
    // Seçili ama artık filtrede olmayan satırları bul
    const rowsToDeselect = selectedRows.filter(row => !filteredRowIds.has(row.id));
    
    if (rowsToDeselect.length > 0) {
      // Bu satırların seçimini kaldır
      rowsToDeselect.forEach(row => {
        row.toggleSelected(false);
      });
      console.log(`🔄 ${rowsToDeselect.length} satır filtreye uymadığı için seçimden çıkarıldı`);
    }
  }, [filesData, statusFilter, table]);

  // Filter senkronizasyonu - statusFilter değiştiğinde veya table yeniden oluştuğunda filter'ı uygula
  useEffect(() => {
    if (statusFilter && statusFilter !== '') {
      table.getColumn('status')?.setFilterValue(statusFilter);
      // Filter değiştiğinde mevcut seçimleri temizle
      table.resetRowSelection();
    }
  }, [statusFilter, table]);

  // Diğer filterler için de senkronizasyon
  useEffect(() => {
    if (filetypeFilter && filetypeFilter !== '') {
      table.getColumn('type')?.setFilterValue(filetypeFilter);
      table.resetRowSelection();
    }
  }, [filetypeFilter, table]);

  useEffect(() => {
    if (fileSourceFilter && fileSourceFilter !== '') {
      table.getColumn('source')?.setFilterValue(fileSourceFilter);
      table.resetRowSelection();
    }
  }, [fileSourceFilter, table]);

  useEffect(() => {
    if (llmtypeFilter && llmtypeFilter !== '') {
      table.getColumn('model')?.setFilterValue(llmtypeFilter);
      table.resetRowSelection();
    }
  }, [llmtypeFilter, table]);

  useEffect(() => {
    table.getColumn('name')?.setFilterValue(nameFilter);
  }, [nameFilter, table]);

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

  // V2 dosyaları için periyodik polling (her 5 saniyede bir)
  useEffect(() => {
    // V2 dosyaları var mı kontrol et
    const hasV2Files = filesData.some((f) => f.fileSource === 'V2 Queue');
    if (!hasV2Files) {
      return;
    }

    // Her 5 saniyede bir durumları güncelle (tüm V2 dosyaları için)
    const interval = setInterval(() => {
      reloadV2Files();
    }, 5000);

    return () => clearInterval(interval);
  }, [filesData, reloadV2Files]);

  // Filtre veya sayfa değiştiğinde, görünen dosyaların detaylarını yükle
  const loadMissingDetails = useCallback(async () => {
    // Filtrelenmiş ve sayfalanmış satırları al
    const visibleRows = table.getRowModel().rows;
    
    // Detayı olmayan dosyaları bul (v2FileId varsa ve _detail yoksa)
    const missingDetailIds: number[] = [];
    visibleRows.forEach(row => {
      const file = row.original;
      // V2 file ve detay yüklenmemişse VE cache'de de yoksa
      if (file.v2FileId && !file._detail && file.fileSource === 'V2 Queue') {
        if (!detailCacheRef.current.has(file.v2FileId)) {
          missingDetailIds.push(file.v2FileId);
        }
      }
    });

    if (missingDetailIds.length === 0) {
      return;
    }

    console.log(`📄 Loading missing details for ${missingDetailIds.length} files:`, missingDetailIds);
    
    try {
      const response = await getFileDetailsByIdsAPI(missingDetailIds);
      if (response?.status === 'Success' && response?.data?.files) {
        // Detayları cache'e ekle
        response.data.files.forEach((f: any) => {
          detailCacheRef.current.set(f.id, f);
        });

        // filesData'yı güncelle
        setFilesData(prevFiles => prevFiles.map(file => {
          if (file.v2FileId && detailCacheRef.current.has(file.v2FileId)) {
            const detail = detailCacheRef.current.get(file.v2FileId);
            return {
              ...file,
              name: detail.original_name || file.name,
              size: detail.file_size || file.size,
              chunking_completed_at: detail.chunking_completed_at,
              graph_completed_at: detail.graph_completed_at,
              embedding_completed_at: detail.embedding_completed_at,
              _detail: true,
            };
          }
          return file;
        }));
        
        console.log(`✅ Loaded ${response.data.files.length} details, cache size: ${detailCacheRef.current.size}`);
      }
    } catch (error) {
      console.error('Failed to load missing details:', error);
    }
  }, [table, setFilesData]);

  // Filtre değiştiğinde detayları yükle
  useEffect(() => {
    // Filtre aktifse ve tablo hazırsa detayları yükle
    const hasActiveFilter = statusFilter || filetypeFilter !== 'All' || fileSourceFilter !== 'All' || nameFilter;
    if (hasActiveFilter) {
      // Kısa bir gecikme ile yükle (tablo render olduktan sonra)
      const timer = setTimeout(() => {
        loadMissingDetails();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [statusFilter, filetypeFilter, fileSourceFilter, nameFilter, currentPageIndex, loadMissingDetails]);

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
            // Tüm dosyalar seçilmişse "all" parametresi kullan
            const response = await startChunkingAPI('all');
            if (response.status === 'Success' || response.status === 'success' || response.data?.status === 'success') {
              const processedCount = response.data?.processed_count || selected.length;
              showNormalToast(`✓ Chunking started for ${processedCount} file(s)`);
            } else {
              showErrorToast(`Failed to start chunking: ${response.message || 'Unknown error'}`);
            }
          } else {
            // Aradan seçim yapılmışsa, her dosya için tek tek istek gönder
            showNormalToast(`${selected.length} dosya için chunking başlatılıyor...`);
            let successCount = 0;
            let failCount = 0;
            const failedFiles: Array<{ id: number; reason: string }> = [];

            for (const fileId of selected) {
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
              showNormalToast(`✓ Chunking started for ${successCount} file(s)`);
            } else {
              const errorDetails = failedFiles.map((f) => `File ${f.id}: ${f.reason}`).join('; ');
              showErrorToast(
                `Chunking started for ${successCount} file(s), failed for ${failCount} file(s). ${errorDetails}`
              );
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

        try {
          // Her zaman seçilen dosyaların ID'lerini gönder (artık 'all' kullanmıyoruz)
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

          // Seçilen dosyaların ID'lerini gönder (virgülle ayrılmış veya tek ID)
          const fileIds = chunkedFiles.map((f) => f.v2FileId).filter((id): id is number => id !== undefined);
          const fileIdParam = fileIds.length === 1 ? String(fileIds[0]) : fileIds.join(',');
          
          showNormalToast(`${chunkedFiles.length} dosya için graph oluşturuluyor...`);
          console.log(`📤 Sending graph creation request for file IDs: ${fileIdParam}`);
          const response = await startGraphCreationAPI(fileIdParam, 'openai_gpt_4o_mini', false);
          if (response.status === 'Success' || response.status === 'success' || response.data?.status === 'success') {
            const processedCount = response.data?.processed_count || chunkedFiles.length;
            showNormalToast(`✓ Graph creation started for ${processedCount} file(s)`);
          } else {
            showErrorToast(`Failed to start graph creation: ${response.message || 'Unknown error'}`);
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

