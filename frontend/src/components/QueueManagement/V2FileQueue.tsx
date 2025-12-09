/* eslint-disable no-console */
import { Button, Checkbox, Flex, IconButton, ProgressBar, StatusIndicator, Typography } from '@neo4j-ndl/react';
import { ArrowPathIconSolid, PlayIconSolid, SparklesIconSolid, TrashIconOutline } from '@neo4j-ndl/react/icons';
import React, { useCallback, useEffect, useState } from 'react';
import { useCredentials } from '../../context/UserCredentials';
import {
  deleteFileFromQueueAPI,
  getQueuedFilesAPI,
  resetFileStageAPI,
  startChunkingAPI,
  startGraphCreationAPI,
} from '../../utils/FileAPI';
import { showErrorToast, showSuccessToast } from '../../utils/Toasts';

interface V2File {
  id: number;
  original_name?: string;
  file_size?: number;
  upload_date?: string;
  upload_status: 'uploaded' | 'uploading' | 'failed';
  chunking_status: 'pending' | 'chunking' | 'chunked' | 'failed';
  graph_status: 'pending' | 'processing' | 'completed' | 'failed';
  processing_error?: string;
  _detail?: boolean; // true if full details are loaded
}

const PAGE_SIZE = 50; // Items per page

const V2FileQueue: React.FC = () => {
  const [files, setFiles] = useState<V2File[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedFileIds, setSelectedFileIds] = useState<Set<number>>(new Set());
  const [currentPage, setCurrentPage] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const { userCredentials, watchProcessingMode } = useCredentials();

  // Fetch V2 files with pagination
  const fetchV2Files = useCallback(async () => {
    try {
      setIsLoading(true);
      const detailOffset = currentPage * PAGE_SIZE;
      console.log(`📄 Fetching files: page=${currentPage}, offset=${detailOffset}, limit=${PAGE_SIZE}`);
      const response = await getQueuedFilesAPI(PAGE_SIZE, detailOffset);
      if (response?.status === 'Success' && response?.data?.files) {
        setFiles(response.data.files);
        setTotalCount(response.data.total_count || response.data.files.length);
        console.log(`✅ Received ${response.data.files.length} files, detail_offset=${response.data.detail_offset}`);
      }
    } catch (error) {
      showErrorToast('Failed to fetch queue files');
    } finally {
      setIsLoading(false);
    }
  }, [currentPage]);

  useEffect(() => {
    console.log(`🔄 Page changed to ${currentPage}, fetching new data...`);
    fetchV2Files();
    
    // Watch mode kapalıysa polling yapma
    if (!watchProcessingMode) {
      return;
    }
    
    // Poll every 5 seconds for status updates
    const interval = setInterval(fetchV2Files, 5000);
    return () => clearInterval(interval);
  }, [fetchV2Files, watchProcessingMode]);

  // Calculate pagination - files from detail range have full data
  const totalPages = Math.ceil(totalCount / PAGE_SIZE);
  // Backend'den gelen dosyalar zaten sıralı, sadece mevcut sayfa aralığını göster
  const startIdx = currentPage * PAGE_SIZE;
  const endIdx = Math.min(startIdx + PAGE_SIZE, files.length);
  const displayedFiles = files.slice(startIdx, endIdx);

  const handleSelectFile = (fileId: number) => {
    const newSelected = new Set(selectedFileIds);
    if (newSelected.has(fileId)) {
      newSelected.delete(fileId);
    } else {
      newSelected.add(fileId);
    }
    setSelectedFileIds(newSelected);
  };

  const handleSelectAll = () => {
    if (selectedFileIds.size === files.length) {
      setSelectedFileIds(new Set());
    } else {
      setSelectedFileIds(new Set(files.map((f) => f.id)));
    }
  };

  const handleStartChunking = async () => {
    if (selectedFileIds.size === 0) {
      showErrorToast('Please select at least one file');
      return;
    }

    try {
      setIsLoading(true);
      const fileIds = Array.from(selectedFileIds);
      console.log(`🔄 Starting chunking for ${fileIds.length} files`);

      // Check if all files are selected
      const allFiles = files.map((f) => f.id);
      const isAllSelected = allFiles.length > 0 && fileIds.length === allFiles.length;

      if (isAllSelected) {
        // Tüm dosyalar seçilmişse "all" parametresi kullan
        const response = await startChunkingAPI('all');
        if (response?.status === 'Success' || response?.status === 'success' || response?.data?.status === 'success') {
          const processedCount = response.data?.processed_count || fileIds.length;
          showSuccessToast(`Started chunking for ${processedCount} file(s)`);
        } else {
          showErrorToast(`Failed to start chunking: ${response?.message || 'Unknown error'}`);
        }
      } else {
        // Aradan seçim yapılmışsa, her dosya için tek tek istek gönder
        let successCount = 0;
        let failCount = 0;

        for (const fileId of fileIds) {
          try {
            const response = await startChunkingAPI(fileId);
            if (response?.status === 'Success' || response?.status === 'success' || response?.data?.status === 'success') {
              successCount++;
            } else {
              failCount++;
              console.error(`Failed to start chunking for file ${fileId}:`, response?.message);
            }
          } catch (error) {
            failCount++;
            console.error(`Error starting chunking for file ${fileId}:`, error);
          }
        }

        if (failCount === 0) {
          showSuccessToast(`Started chunking for ${successCount} file(s)`);
        } else {
          showErrorToast(`Started chunking for ${successCount} file(s), failed for ${failCount} file(s)`);
        }
      }

      setSelectedFileIds(new Set());
      await fetchV2Files(); // Refresh file list
    } catch (error) {
      showErrorToast('Failed to start chunking');
    } finally {
      setIsLoading(false);
    }
  };

  const handleCreateGraph = async () => {
    if (selectedFileIds.size === 0) {
      showErrorToast('Please select at least one file');
      return;
    }

    try {
      setIsLoading(true);
      const fileIds = Array.from(selectedFileIds);
      
      // Check if all files are selected
      const allFiles = files.map((f) => f.id);
      const isAllSelected = allFiles.length > 0 && fileIds.length === allFiles.length;

      if (isAllSelected) {
        // Tüm dosyalar seçilmişse "all" parametresi kullan
        console.log(`✨ Starting graph creation for ALL ${fileIds.length} files`);
        const response = await startGraphCreationAPI('all', 'openai_gpt_4o_mini', false);

        if (response?.status === 'Success' || response?.status === 'success' || response?.data?.status === 'success') {
          const processedCount = response.data?.processed_count || fileIds.length;
          showSuccessToast(`Started graph creation for ${processedCount} file(s) (all files - batch)`);
        } else {
          showErrorToast(`Failed to start graph creation: ${response?.message || 'Unknown error'}`);
        }
      } else {
        // Aradan seçim yapılmışsa virgüllü ID'ler gönder
        console.log(`✨ Starting graph creation for ${fileIds.length} selected files: ${fileIds.join(', ')}`);
        const fileIdParam = fileIds.length === 1 ? fileIds[0] : fileIds.join(',');
        const response = await startGraphCreationAPI(fileIdParam, 'openai_gpt_4o_mini', false);

        if (response?.status === 'Success' || response?.status === 'success' || response?.data?.status === 'success') {
          const processedCount = response.data?.processed_count || fileIds.length;
          showSuccessToast(`Started graph creation for ${processedCount} file(s) (batch processing)`);
        } else {
          showErrorToast(`Failed to start graph creation: ${response?.message || 'Unknown error'}`);
        }
      }

      setSelectedFileIds(new Set());
      await fetchV2Files(); // Refresh file list
    } catch (error) {
      console.error('❌ Graph creation error:', error);
      const errorMessage = error instanceof Error ? error.message : 'Failed to start graph creation';
      showErrorToast(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  const handleResetStage = async (fileId: number, stage: 'upload' | 'chunking' | 'graph') => {
    if (!confirm(`Reset ${stage} stage for this file?`)) {
      return;
    }

    // Chunking reset için markdown silme seçeneği sor
    let deleteMarkdown = false;
    if (stage === 'chunking') {
      deleteMarkdown = confirm(
        'Çıkartılan markdown dosyasını da silmek istiyor musunuz?\n\n' +
        '• EVET: Markdown silinir, chunking baştan yapılır\n' +
        '• HAYIR: Markdown korunur, sadece durum sıfırlanır'
      );
    }

    try {
      setIsLoading(true);
      console.log(`🔄 Resetting ${stage} stage for file ${fileId}${stage === 'chunking' ? ` (deleteMarkdown=${deleteMarkdown})` : ''}`);

      // Call backend API
      await resetFileStageAPI(fileId, stage, deleteMarkdown);

      showSuccessToast(`${stage} stage reset${deleteMarkdown ? ' (markdown silindi)' : ''}`);
      await fetchV2Files(); // Refresh file list
    } catch (error) {
      showErrorToast(`Failed to reset ${stage} stage`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteFile = async (fileId: number, filename: string) => {
    if (!confirm(`Delete "${filename}"?`)) {
      return;
    }

    try {
      setIsLoading(true);
      console.log(`🗑️ Deleting file ${fileId}`);

      // Call backend API
      await deleteFileFromQueueAPI(fileId);

      showSuccessToast('File deleted');
      await fetchV2Files(); // Refresh file list
    } catch (error) {
      showErrorToast('Failed to delete file');
    } finally {
      setIsLoading(false);
    }
  };

  const getStageColor = (status: string) => {
    switch (status) {
      case 'pending':
        return 'unknown';
      case 'uploading':
      case 'chunking':
      case 'processing':
        return 'info';
      case 'chunked':
      case 'completed':
        return 'success';
      case 'failed':
        return 'danger';
      default:
        return 'unknown';
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) {
      return '0 Bytes';
    }
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
  };

  return (
    <div className='w-full p-4'>
      {/* Header with Controls */}
      <Flex justifyContent='space-between' alignItems='center' className='mb-4'>
        <Typography variant='h5'>📋 V2 File Queue Management</Typography>
        <Flex className='gap-2'>
          <Button onClick={handleStartChunking} isDisabled={isLoading || selectedFileIds.size === 0} size='small'>
            <PlayIconSolid className='w-4 h-4 mr-1' />
            Start Chunking ({selectedFileIds.size})
          </Button>
          <Button onClick={handleCreateGraph} isDisabled={isLoading || selectedFileIds.size === 0} size='small'>
            <SparklesIconSolid className='w-4 h-4 mr-1' />
            Create Graph ({selectedFileIds.size})
          </Button>
        </Flex>
      </Flex>

      {/* Files Table */}
      <div className='border rounded-lg overflow-hidden'>
        {/* Header */}
        <div className='bg-gray-50 border-b p-4'>
          <div className='grid grid-cols-12 gap-3 items-center'>
            <div className='col-span-1'>
              <Checkbox
                isChecked={selectedFileIds.size === files.length && files.length > 0}
                onChange={handleSelectAll}
              />
            </div>
            <Typography variant='body-medium' className='col-span-2 font-semibold'>
              Filename
            </Typography>
            <Typography variant='body-medium' className='col-span-1 font-semibold'>
              Date
            </Typography>
            <Typography variant='body-medium' className='col-span-2 font-semibold'>
              Upload
            </Typography>
            <Typography variant='body-medium' className='col-span-2 font-semibold'>
              Chunking
            </Typography>
            <Typography variant='body-medium' className='col-span-2 font-semibold'>
              Graph
            </Typography>
            <Typography variant='body-medium' className='col-span-2 font-semibold'>
              Actions
            </Typography>
          </div>
        </div>

        {/* Rows */}
        <div className='divide-y'>
          {files.length === 0 ? (
            <div className='p-8 text-center'>
              <Typography variant='body-large' className='text-gray-500'>
                No files in queue
              </Typography>
            </div>
          ) : (
            displayedFiles.map((file) => (
              <div key={file.id} className='p-4 hover:bg-gray-50 transition-colors'>
                <div className='grid grid-cols-12 gap-3 items-center'>
                  {/* Checkbox */}
                  <div className='col-span-1'>
                    <Checkbox isChecked={selectedFileIds.has(file.id)} onChange={() => handleSelectFile(file.id)} />
                  </div>

                  {/* Filename */}
                  <div className='col-span-2'>
                    <Typography variant='body-medium' className='font-medium truncate'>
                      {file.original_name || `File #${file.id}`}
                    </Typography>
                    <Typography variant='body-small' className='text-gray-500'>
                      {file.file_size ? formatFileSize(file.file_size) : '-'}
                    </Typography>
                  </div>

                  {/* Upload Date */}
                  <Typography variant='body-small' className='col-span-1'>
                    {file.upload_date ? new Date(file.upload_date).toLocaleDateString('tr-TR') : '-'}
                  </Typography>

                  {/* Upload Stage */}
                  <div className='col-span-2'>
                    <Flex alignItems='center' className='gap-2'>
                      <StatusIndicator type={getStageColor(file.upload_status)} />
                      <div className='flex-1'>
                        <Typography variant='body-small' className='capitalize'>
                          {file.upload_status}
                        </Typography>
                        <ProgressBar size='small' value={file.upload_status === 'uploaded' ? 100 : 50} />
                      </div>
                    </Flex>
                  </div>

                  {/* Chunking Stage */}
                  <div className='col-span-2'>
                    <Flex alignItems='center' className='gap-2'>
                      <StatusIndicator type={getStageColor(file.chunking_status)} />
                      <div className='flex-1'>
                        <Typography variant='body-small' className='capitalize'>
                          {file.chunking_status}
                        </Typography>
                        {file.chunking_status === 'chunking' && <ProgressBar size='small' value={50} />}
                      </div>
                    </Flex>
                  </div>

                  {/* Graph Stage */}
                  <div className='col-span-2'>
                    <Flex alignItems='center' className='gap-2'>
                      <StatusIndicator type={getStageColor(file.graph_status)} />
                      <div className='flex-1'>
                        <Typography variant='body-small' className='capitalize'>
                          {file.graph_status}
                        </Typography>
                        {file.graph_status === 'processing' && <ProgressBar size='small' value={50} />}
                      </div>
                    </Flex>
                  </div>

                  {/* Actions */}
                  <Flex className='col-span-2 gap-1 justify-end'>
                    {file.upload_status === 'uploaded' && (
                      <IconButton
                        size='small'
                        ariaLabel='Reset upload'
                        onClick={() => handleResetStage(file.id, 'upload')}
                        isDisabled={isLoading}
                      >
                        <ArrowPathIconSolid className='w-4 h-4' />
                      </IconButton>
                    )}
                    {file.chunking_status === 'chunked' && (
                      <IconButton
                        size='small'
                        ariaLabel='Reset chunking'
                        onClick={() => handleResetStage(file.id, 'chunking')}
                        isDisabled={isLoading}
                      >
                        <ArrowPathIconSolid className='w-4 h-4' />
                      </IconButton>
                    )}
                    {file.graph_status === 'completed' && (
                      <IconButton
                        size='small'
                        ariaLabel='Reset graph'
                        onClick={() => handleResetStage(file.id, 'graph')}
                        isDisabled={isLoading}
                      >
                        <ArrowPathIconSolid className='w-4 h-4' />
                      </IconButton>
                    )}
                    <IconButton
                      size='small'
                      ariaLabel='Delete file'
                      onClick={() => handleDeleteFile(file.id, file.original_name || `File #${file.id}`)}
                      isDisabled={isLoading}
                    >
                      <TrashIconOutline className='w-4 h-4' />
                    </IconButton>
                  </Flex>
                </div>
              </div>
            ))
          )}
        </div>

        {/* Pagination Controls */}
        {totalPages > 1 && (
          <div className='bg-gray-50 border-t p-3'>
            <Flex justifyContent='space-between' alignItems='center'>
              <Typography variant='body-small' className='text-gray-600'>
                Showing {currentPage * PAGE_SIZE + 1}-{Math.min((currentPage + 1) * PAGE_SIZE, totalCount)} of {totalCount} files
              </Typography>
              <Flex className='gap-2'>
                <Button
                  size='small'
                  onClick={() => setCurrentPage(0)}
                  isDisabled={currentPage === 0 || isLoading}
                >
                  ⏮ First
                </Button>
                <Button
                  size='small'
                  onClick={() => setCurrentPage(Math.max(0, currentPage - 1))}
                  isDisabled={currentPage === 0 || isLoading}
                >
                  ◀ Prev
                </Button>
                <Typography variant='body-medium' className='px-3 py-1 bg-white border rounded'>
                  Page {currentPage + 1} / {totalPages}
                </Typography>
                <Button
                  size='small'
                  onClick={() => setCurrentPage(Math.min(totalPages - 1, currentPage + 1))}
                  isDisabled={currentPage >= totalPages - 1 || isLoading}
                >
                  Next ▶
                </Button>
                <Button
                  size='small'
                  onClick={() => setCurrentPage(totalPages - 1)}
                  isDisabled={currentPage >= totalPages - 1 || isLoading}
                >
                  Last ⏭
                </Button>
              </Flex>
            </Flex>
          </div>
        )}
      </div>

      {/* Info */}
      <div className='mt-6 p-4 bg-blue-50 rounded-lg'>
        <Typography variant='h6' className='text-blue-800 mb-2'>
          📖 V2 Queue Workflow
        </Typography>
        <Typography variant='body-medium' className='text-blue-700'>
          <strong>Stage 1 - Upload:</strong> Files uploaded to queue
          <br />
          <strong>Stage 2 - Chunking:</strong> Files split into chunks for processing
          <br />
          <strong>Stage 3 - Graph:</strong> Graph nodes and relationships created in Neo4j
          <br />
          <br />
          💡 You can reset any stage. Resetting a stage will also reset all subsequent stages.
        </Typography>
      </div>
    </div>
  );
};

export default V2FileQueue;
