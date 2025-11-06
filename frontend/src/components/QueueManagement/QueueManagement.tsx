/* eslint-disable no-console, prefer-template */
import { Button, Flex, IconButton, StatusIndicator, Typography } from '@neo4j-ndl/react';
import { ArrowPathIconSolid, PlayIconSolid, StopIconSolid, TrashIconOutline } from '@neo4j-ndl/react/icons';
import React, { useCallback, useEffect, useState } from 'react';
import { useCredentials } from '../../context/UserCredentials';
import {
  deleteFileFromQueueAPI,
  getBackgroundProcessingStatusAPI,
  getQueuedFilesAPI,
  getQueueStatusAPI,
  queueFileForProcessingAPI,
  startBackgroundProcessingAPI,
  stopBackgroundProcessingAPI,
} from '../../utils/FileAPI';
import { showErrorToast, showSuccessToast } from '../../utils/Toasts';

interface QueuedFile {
  id: number;
  filename: string;
  original_name: string;
  file_path: string;
  upload_date: string;
  file_size: number;
  file_hash: string;
  status: string;
  created_at: string;
  updated_at: string;
  processing_started_at?: string;
  processing_completed_at?: string;
  processing_error?: string;
}

interface QueueStats {
  uploaded: number;
  queued: number;
  processing: number;
  completed: number;
  error: number;
  total: number;
}

interface ProcessingStatus {
  is_processing: boolean;
  current_task_id?: number;
  queue_stats: QueueStats;
}

const QueueManagement: React.FC = () => {
  const [queuedFiles, setQueuedFiles] = useState<QueuedFile[]>([]);
  const [queueStats, setQueueStats] = useState<QueueStats>({
    uploaded: 0,
    queued: 0,
    processing: 0,
    completed: 0,
    error: 0,
    total: 0,
  });
  const [processingStatus, setProcessingStatus] = useState<ProcessingStatus>({
    is_processing: false,
    queue_stats: { uploaded: 0, queued: 0, processing: 0, completed: 0, error: 0, total: 0 },
  });
  const [selectedFiles, setSelectedFiles] = useState<number[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const { userCredentials } = useCredentials();

  // Polling interval
  useEffect(() => {
    fetchQueueData();
    fetchProcessingStatus();

    const interval = setInterval(() => {
      fetchQueueData();
      fetchProcessingStatus();
    }, 5000); // Poll every 5 seconds

    return () => clearInterval(interval);
  }, []);

  const fetchQueueData = useCallback(async () => {
    try {
      const [filesResponse, statsResponse] = await Promise.all([getQueuedFilesAPI(), getQueueStatusAPI()]);

      if (filesResponse?.status === 'Success') {
        setQueuedFiles(filesResponse?.data?.files || []);
      }

      if (statsResponse?.status === 'Success') {
        setQueueStats(statsResponse?.data?.queue_stats || {});
      }
    } catch (error: any) {
      console.error('Error fetching queue data:', error);
    }
  }, []);

  const fetchProcessingStatus = useCallback(async () => {
    try {
      const response = await getBackgroundProcessingStatusAPI();
      if (response?.status === 'Success') {
        setProcessingStatus(response?.data?.processing_status || {});
      }
    } catch (error: any) {
      console.error('Error fetching processing status:', error);
    }
  }, []);

  const handleStartProcessing = async () => {
    try {
      setIsLoading(true);
      const response = await startBackgroundProcessingAPI();

      if (response?.status === 'Success') {
        showSuccessToast('Background processing started');
        fetchProcessingStatus();
      } else {
        showErrorToast(`Failed to start processing: ${response?.message}`);
      }
    } catch (error: any) {
      showErrorToast(`Error starting processing: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleStopProcessing = async () => {
    try {
      setIsLoading(true);
      const response = await stopBackgroundProcessingAPI();

      if (response?.status === 'Success') {
        showSuccessToast('Background processing stopped');
        fetchProcessingStatus();
      } else {
        showErrorToast(`Failed to stop processing: ${response?.message}`);
      }
    } catch (error: any) {
      showErrorToast(`Error stopping processing: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleQueueFile = async (fileId: number) => {
    if (!userCredentials) {
      showErrorToast('User credentials not available');
      return;
    }

    try {
      setIsLoading(true);

      const response = await queueFileForProcessingAPI(
        fileId,
        'openai_gpt_4o_mini',
        userCredentials.uri || 'bolt://localhost:7687',
        userCredentials.userName || 'neo4j',
        userCredentials.password || '',
        userCredentials.database || 'neo4j',
        false
      );

      if (response?.status === 'Success') {
        showSuccessToast('File queued for processing');
        fetchQueueData();
      } else {
        showErrorToast(`Failed to queue file: ${response?.message}`);
      }
    } catch (error: any) {
      showErrorToast(`Error queuing file: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteFile = async (fileId: number, filename: string) => {
    if (!confirm(`Are you sure you want to delete "${filename}"?`)) {
      return;
    }

    try {
      setIsLoading(true);
      const response = await deleteFileFromQueueAPI(fileId);

      if (response?.status === 'Success') {
        showSuccessToast('File deleted successfully');
        fetchQueueData();
      } else {
        showErrorToast(`Failed to delete file: ${response?.message}`);
      }
    } catch (error: any) {
      showErrorToast(`Error deleting file: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const getStatusColor = (status: string): 'success' | 'info' | 'warning' | 'danger' | 'unknown' => {
    switch (status) {
      case 'uploaded':
        return 'info';
      case 'queued':
        return 'warning';
      case 'processing':
        return 'info';
      case 'completed':
        return 'success';
      case 'error':
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

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleString();
  };

  return (
    <div className='w-full p-6'>
      <Flex flexDirection='column' className='w-full'>
        {/* Header */}
        <Flex justifyContent='space-between' alignItems='center' className='mb-6'>
          <Typography variant='h4'>📋 File Upload Queue Management</Typography>

          <Flex className='gap-2'>
            <Button onClick={fetchQueueData} isDisabled={isLoading} size='small'>
              <ArrowPathIconSolid className='w-4 h-4 mr-1' />
              Refresh
            </Button>

            {processingStatus.is_processing ? (
              <Button onClick={handleStopProcessing} isDisabled={isLoading} fill='outlined' size='small'>
                <StopIconSolid className='w-4 h-4 mr-1' />
                Stop Processing
              </Button>
            ) : (
              <Button onClick={handleStartProcessing} isDisabled={isLoading} size='small'>
                <PlayIconSolid className='w-4 h-4 mr-1' />
                Start Processing
              </Button>
            )}
          </Flex>
        </Flex>

        {/* Queue Statistics */}
        <div className='mb-6 p-4 bg-gray-50 rounded-lg'>
          <Typography variant='h6' className='mb-3'>
            📊 Queue Statistics
          </Typography>
          <Flex className='gap-4 flex-wrap'>
            <div className='flex items-center gap-2'>
              <StatusIndicator type='info' />
              <span>Uploaded: {queueStats.uploaded}</span>
            </div>
            <div className='flex items-center gap-2'>
              <StatusIndicator type='warning' />
              <span>Queued: {queueStats.queued}</span>
            </div>
            <div className='flex items-center gap-2'>
              <StatusIndicator type='info' />
              <span>Processing: {queueStats.processing}</span>
            </div>
            <div className='flex items-center gap-2'>
              <StatusIndicator type='success' />
              <span>Completed: {queueStats.completed}</span>
            </div>
            <div className='flex items-center gap-2'>
              <StatusIndicator type='danger' />
              <span>Error: {queueStats.error}</span>
            </div>
            <div className='flex items-center gap-2'>
              <StatusIndicator type='unknown' />
              <span>Total: {queueStats.total}</span>
            </div>
          </Flex>

          {processingStatus.is_processing && (
            <div className='mt-3 p-2 bg-blue-100 rounded'>
              <Typography variant='body-medium' className='text-blue-800'>
                🔄 Background processing is running
                {processingStatus.current_task_id && ` (Current task: ${processingStatus.current_task_id})`}
              </Typography>
            </div>
          )}
        </div>

        {/* Files List */}
        <div className='border rounded-lg overflow-hidden'>
          <div className='bg-gray-50 border-b p-4'>
            <div className='grid grid-cols-5 gap-4'>
              <Typography variant='body-medium' className='font-semibold'>
                Filename
              </Typography>
              <Typography variant='body-medium' className='font-semibold'>
                Size
              </Typography>
              <Typography variant='body-medium' className='font-semibold'>
                Status
              </Typography>
              <Typography variant='body-medium' className='font-semibold'>
                Upload Date
              </Typography>
              <Typography variant='body-medium' className='font-semibold'>
                Actions
              </Typography>
            </div>
          </div>
          <div className='divide-y'>
            {queuedFiles.length === 0 ? (
              <div className='p-8 text-center'>
                <Typography variant='body-large' className='text-gray-500'>
                  No files in queue. Upload some files to get started.
                </Typography>
              </div>
            ) : (
              queuedFiles.map((file) => (
                <div key={file.id} className='p-4 hover:bg-gray-50 transition-colors'>
                  <div className='grid grid-cols-5 gap-4 items-start'>
                    <div>
                      <Typography variant='body-medium' className='font-medium'>
                        {file.original_name}
                      </Typography>
                      <Typography variant='body-small' className='text-gray-500'>
                        ID: {file.id}
                      </Typography>
                    </div>
                    <Typography variant='body-medium'>{formatFileSize(file.file_size)}</Typography>
                    <div>
                      <Flex alignItems='center' className='gap-2'>
                        <StatusIndicator type={getStatusColor(file.status)} />
                        <span className='capitalize text-sm'>{file.status}</span>
                      </Flex>
                      {file.processing_error && (
                        <Typography variant='body-small' className='text-red-600 mt-1'>
                          Error: {file.processing_error}
                        </Typography>
                      )}
                    </div>
                    <Typography variant='body-medium'>{formatDate(file.upload_date)}</Typography>
                    <Flex className='gap-1'>
                      {file.status === 'uploaded' && (
                        <IconButton
                          size='small'
                          ariaLabel='Queue for processing'
                          onClick={() => handleQueueFile(file.id)}
                          isDisabled={isLoading}
                        >
                          <PlayIconSolid className='w-4 h-4' />
                        </IconButton>
                      )}
                      <IconButton
                        size='small'
                        ariaLabel='Delete file'
                        onClick={() => handleDeleteFile(file.id, file.original_name)}
                        isDisabled={isLoading || file.status === 'processing'}
                      >
                        <TrashIconOutline className='w-4 h-4' />
                      </IconButton>
                    </Flex>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Instructions */}
        <div className='mt-6 p-4 bg-blue-50 rounded-lg'>
          <Typography variant='h6' className='text-blue-800 mb-2'>
            💡 How to Use Queue Management
          </Typography>
          <Typography variant='body-medium' className='text-blue-700'>
            1. Upload files using the V2 Upload component
            <br />
            2. Files appear here with "uploaded" status
            <br />
            3. Click the play button to queue files for processing
            <br />
            4. Start background processing to process queued files
            <br />
            5. Monitor progress and manage files from this dashboard
          </Typography>
        </div>
      </Flex>
    </div>
  );
};

export default QueueManagement;
