/* eslint-disable no-console */
import { Button, Flex, SpotlightTarget, Typography } from '@neo4j-ndl/react';
import { ArrowRightIconOutline, InformationCircleIconOutline } from '@neo4j-ndl/react/icons';
import { FunctionComponent, useCallback, useEffect, useState } from 'react';
import Dropzone from 'react-dropzone';
import { useCredentials } from '../../../context/UserCredentials';
import { useFileContext } from '../../../context/UsersFiles';
import { CustomFile } from '../../../types';
import { chunkSize } from '../../../utils/Constants';
import { uploadFileToQueueAPI } from '../../../utils/FileAPI';
import Loader from '../../../utils/Loader';
import { showErrorToast, showSuccessToast } from '../../../utils/Toasts';
import { normalizeFileName } from '../../../utils/utf8';

interface UploadedFileInfo {
  id: number;
  filename: string;
  original_name: string;
  status: string;
  duplicate: boolean;
  file_size?: number;
  upload_status?: string;
  chunking_status?: string;
  graph_status?: string;
}

const DropZoneV2: FunctionComponent = () => {
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const { userCredentials } = useCredentials();
  const { filesData, setFilesData, model } = useFileContext();
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);

  // V2 Upload state
  const [uploadQueue, setUploadQueue] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileInfo[]>([]);
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0 });
  const [failedFiles, setFailedFiles] = useState<string[]>([]);

  const BATCH_SIZE = 10; // Smaller batch size for V2

  const onDropHandler = (f: Partial<globalThis.File>[]) => {
    setIsLoading(false);
    console.log(`📁 V2 Files dropped: ${f.length} files`);

    // File validation
    const validFiles: File[] = [];
    f.forEach((file, index) => {
      if (!file || !file.name) {
        console.error(`❌ File ${index + 1} is invalid or missing name - skipping`);
        return;
      }

      console.log(`📄 File ${index + 1}: ${file.name}, Size: ${file.size ?? 'undefined'} bytes, Type: ${file.type}`);

      // File size validation
      if (!file.size || file.size === 0) {
        console.error(`❌ File ${file.name} is empty (${file.size ?? 'undefined'} bytes) - skipping`);
        showErrorToast(`File "${file.name}" is empty and cannot be uploaded`);
      } else if (file.size > 100 * 1024 * 1024) {
        console.error(`❌ File ${file.name} is too large (${(file.size / (1024 * 1024)).toFixed(2)}MB) - skipping`);
        showErrorToast(`File "${file.name}" is too large. Maximum size is 100MB`);
      } else {
        validFiles.push(file as File);
        console.log(`✅ File ${file.name} validated and added to queue`);
      }
    });

    if (validFiles.length === 0) {
      console.warn(`⚠️ No valid files to upload`);
      return;
    }

    setSelectedFiles(validFiles);

    // Add to upload queue
    setUploadQueue((prev) => [...prev, ...validFiles]);
    console.log(`📦 Added ${validFiles.length} files to upload queue`);

    showSuccessToast(`${validFiles.length} files added to upload queue`);
  };

  // V2 Upload function - only uploads, no processing
  const uploadFileInChunksV2 = useCallback(async (file: File): Promise<UploadedFileInfo | null> => {
    console.log(`🚀 V2 Starting upload for: ${file.name} (${(file.size / (1024 * 1024)).toFixed(2)}MB)`);

    try {
      const normalizedFileName = normalizeFileName(file.name);
      console.log(`📝 Normalized filename: ${file.name} -> ${normalizedFileName}`);

      const totalChunks = Math.ceil(file.size / chunkSize);
      console.log(`📊 Upload plan: ${totalChunks} chunks of ${chunkSize} bytes each`);

      let uploadedSize = 0;

      for (let chunkNumber = 1; chunkNumber <= totalChunks; chunkNumber++) {
        const start = (chunkNumber - 1) * chunkSize;
        const end = Math.min(start + chunkSize, file.size);
        const chunk = file.slice(start, end);
        const chunkSizeBytes = chunk.size;

        console.log(`📤 Uploading chunk ${chunkNumber}/${totalChunks}: ${start}-${end} (${chunkSizeBytes} bytes)`);

        const response = await uploadFileToQueueAPI(chunk, chunkNumber, totalChunks, file.name);

        uploadedSize += chunkSizeBytes;

        if (response?.status !== 'Success') {
          throw new Error(`Chunk upload failed: ${response?.message || 'Unknown error'}`);
        }

        console.log(`✅ Chunk ${chunkNumber}/${totalChunks} uploaded successfully`);

        // If this is the last chunk, we get file info
        if (chunkNumber !== totalChunks || !response?.data) {
          continue;
        }

        const fileInfo: UploadedFileInfo = {
          id: response.data.file_id,
          filename: response.data.filename,
          original_name: response.data.original_name || file.name,
          status: response.data.status,
          duplicate: response.data.duplicate || false,
          file_size: response.data.file_size,
          upload_status: response.data.upload_status,
          chunking_status: response.data.chunking_status,
          graph_status: response.data.graph_status,
        };

        console.log(`🎉 File upload completed: ${file.name} -> ID: ${fileInfo.id}`);

        const message = fileInfo.duplicate
          ? `File "${file.name}" already exists in queue`
          : `File "${file.name}" uploaded successfully`;
        showSuccessToast(message);

        // Add to FileContext for FileTable display
        if (!fileInfo.duplicate) {
          const newFile: CustomFile = {
            id: `v2_${fileInfo.id}`, // Backend ile aynı ID formatı kullan
            name: fileInfo.original_name,
            type: fileInfo.original_name.substring(fileInfo.original_name.lastIndexOf('.') + 1).toUpperCase(),
            size: fileInfo.file_size || 0,
            uploadProgress: 100,
            processingProgress: 0,
            status: 'pending', // V2: chunking_status = "pending"
            nodesCount: 0,
            relationshipsCount: 0,
            processingTotalTime: 0,
            model: model || 'openai_gpt_4o_mini',
            fileSource: 'V2 Queue',
            retryOptionStatus: false,
            retryOption: '',
            chunkNodeCount: 0,
            chunkRelCount: 0,
            entityNodeCount: 0,
            entityEntityRelCount: 0,
            communityNodeCount: 0,
            communityRelCount: 0,
            createdAt: new Date(),
            // V2 Queue specific fields
            v2FileId: fileInfo.id,
            upload_status: fileInfo.upload_status,
            chunking_status: fileInfo.chunking_status,
            graph_status: fileInfo.graph_status,
          };
          setFilesData([newFile, ...filesData]);
        }

        return fileInfo;
      }

      return null;
    } catch (error: any) {
      console.error(`❌ V2 Upload failed for ${file.name}:`, error);
      showErrorToast(`Upload failed for "${file.name}": ${error.message}`);
      return null;
    }
  }, []);

  // Batch upload processor for V2
  const processBatchUploadV2 = useCallback(async () => {
    if (isUploading || uploadQueue.length === 0) {
      return;
    }

    setIsUploading(true);
    const totalFiles = uploadQueue.length;
    setFailedFiles([]);

    console.log(`📦 Starting V2 batch upload: ${totalFiles} files, ${BATCH_SIZE} files per batch`);

    const uploadedFilesList: UploadedFileInfo[] = [];

    // Process files in batches
    for (let i = 0; i < uploadQueue.length; i += BATCH_SIZE) {
      const batch = uploadQueue.slice(i, i + BATCH_SIZE);
      const batchNumber = Math.floor(i / BATCH_SIZE) + 1;
      const totalBatches = Math.ceil(uploadQueue.length / BATCH_SIZE);

      console.log(
        `📋 Processing batch ${batchNumber}/${totalBatches}: ${batch.length} files (${batch
          .map((f) => f.name)
          .join(', ')})`
      );

      setBatchProgress({
        current: i,
        total: totalFiles,
      });

      // Upload all files in current batch simultaneously
      const batchPromises = batch.map((file) => {
        return uploadFileInChunksV2(file);
      });

      try {
        const batchResults = await Promise.allSettled(batchPromises);

        batchResults.forEach((result, index) => {
          const fileName = batch[index].name;
          if (result.status === 'fulfilled' && result.value) {
            uploadedFilesList.push(result.value);
            console.log(`✅ Batch upload successful: ${fileName}`);
          } else {
            setFailedFiles((prev) => [...prev, fileName]);
            console.error(`❌ Batch upload failed: ${fileName}`);
            if (result.status === 'rejected') {
              console.error(`❌ Reason:`, result.reason);
            }
          }
        });

        // Add delay between batches to prevent overwhelming the server
        if (i + BATCH_SIZE < uploadQueue.length) {
          console.log(`⏳ Batch ${batchNumber} completed, waiting 1s before next batch...`);
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      } catch (error) {
        console.error(`❌ Batch ${batchNumber} processing error:`, error);
      }
    }

    // Update final progress
    setBatchProgress({
      current: totalFiles,
      total: totalFiles,
    });

    setUploadedFiles((prev) => [...prev, ...uploadedFilesList]);

    const successCount = uploadedFilesList.length;
    const failedCount = failedFiles.length;

    console.log(`🏁 V2 Batch upload completed: ${successCount} successful, ${failedCount} failed`);

    if (successCount > 0) {
      showSuccessToast(`Upload completed: ${successCount} file${successCount > 1 ? 's' : ''} uploaded successfully`);
    }

    if (failedCount > 0) {
      showErrorToast(`${failedCount} file${failedCount > 1 ? 's' : ''} failed to upload`);
    }

    // Clear upload queue
    setUploadQueue([]);
    setSelectedFiles([]);
    setIsUploading(false);
  }, [uploadQueue, isUploading, uploadFileInChunksV2, failedFiles]);

  // Auto-trigger upload when files are added to queue
  useEffect(() => {
    if (uploadQueue.length > 0 && !isUploading) {
      const timer = setTimeout(() => {
        processBatchUploadV2();
      }, 500); // Small delay to allow for multiple file drops

      return () => clearTimeout(timer);
    }
  }, [uploadQueue, isUploading, processBatchUploadV2]);

  const clearQueue = () => {
    setUploadQueue([]);
    setSelectedFiles([]);
    setUploadedFiles([]);
    setFailedFiles([]);
    setBatchProgress({ current: 0, total: 0 });
  };

  return (
    <div className='w-full'>
      <Flex flexDirection='column' className='w-full h-full'>
        <Typography variant='h6' className='mb-4'>
          📁 File Upload Queue (V2)
        </Typography>

        <SpotlightTarget id='upload-v2'>
          <Dropzone
            onDrop={onDropHandler}
            accept={{
              'application/pdf': ['.pdf'],
              'text/plain': ['.txt'],
              'application/msword': ['.doc'],
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
              'text/csv': ['.csv'],
              'application/vnd.ms-excel': ['.xls'],
              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
            }}
            multiple={true}
            disabled={isUploading}
          >
            {({ getRootProps, getInputProps, isDragActive }) => (
              <div
                {...getRootProps()}
                className={`w-full min-h-48 border-2 border-dashed rounded-lg p-8 cursor-pointer transition-colors
                  ${isDragActive ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-gray-400'}
                  ${isUploading ? 'opacity-50 cursor-not-allowed' : ''}
                `}
              >
                <input {...getInputProps()} />
                <Flex flexDirection='column' justifyContent='center' alignItems='center'>
                  <Typography variant='h4' className='mb-2'>
                    {isUploading ? '⏳ Uploading Files...' : isDragActive ? '📥 Drop files now!' : '📤 Drop Files Here'}
                  </Typography>
                  <Typography variant='body-large' className='mb-4 text-center'>
                    {isUploading
                      ? `Uploading ${batchProgress.current}/${batchProgress.total} files`
                      : 'Drag and drop files or click to browse'}
                  </Typography>
                  <Typography variant='body-medium' className='text-center opacity-70'>
                    Supported formats: PDF, TXT, DOC, DOCX, CSV, XLS, XLSX
                    <br />
                    Maximum file size: 100MB
                  </Typography>
                </Flex>
              </div>
            )}
          </Dropzone>
        </SpotlightTarget>

        {/* Upload Progress */}
        {isUploading && (
          <div className='mt-4 p-4 bg-blue-50 rounded-lg'>
            <Typography variant='body-large' className='mb-2'>
              📊 Upload Progress: {batchProgress.current}/{batchProgress.total} files
            </Typography>
            <div className='w-full bg-gray-200 rounded-full h-2'>
              <div
                className='bg-blue-600 h-2 rounded-full transition-all duration-300'
                style={{
                  width: `${batchProgress.total > 0 ? (batchProgress.current / batchProgress.total) * 100 : 0}%`,
                }}
              ></div>
            </div>
          </div>
        )}

        {/* Upload Queue Status */}
        {(uploadQueue.length > 0 || uploadedFiles.length > 0) && (
          <div className='mt-4 p-4 border rounded-lg'>
            <Flex justifyContent='space-between' alignItems='center' className='mb-3'>
              <Typography variant='h6'>📋 Upload Status</Typography>
              <Button size='small' fill='outlined' onClick={clearQueue} isDisabled={isUploading}>
                Clear All
              </Button>
            </Flex>

            {uploadQueue.length > 0 && (
              <Typography variant='body-medium' className='mb-2'>
                ⏳ Queued: {uploadQueue.length} files
              </Typography>
            )}

            {uploadedFiles.length > 0 && (
              <div>
                <Typography variant='body-medium' className='mb-2'>
                  ✅ Uploaded: {uploadedFiles.length} files
                </Typography>
                <div className='max-h-32 overflow-y-auto'>
                  {uploadedFiles.map((file) => (
                    <div key={file.id} className='text-sm p-1 border-l-2 border-green-400 pl-2 mb-1'>
                      <span className={file.duplicate ? 'text-orange-600' : 'text-green-600'}>
                        {file.duplicate ? '📋' : '✅'} {file.original_name}
                        {file.duplicate && ' (already exists)'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {failedFiles.length > 0 && (
              <div className='mt-2'>
                <Typography variant='body-medium' className='mb-2 text-red-600'>
                  ❌ Failed: {failedFiles.length} files
                </Typography>
                <div className='max-h-24 overflow-y-auto'>
                  {failedFiles.map((fileName) => (
                    <div key={fileName} className='text-sm text-red-600 p-1 border-l-2 border-red-400 pl-2 mb-1'>
                      ❌ {fileName}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Navigate to Queue Management */}
            {uploadedFiles.length > 0 && (
              <div className='mt-3 pt-3 border-t'>
                <Flex alignItems='center' className='gap-2'>
                  <Typography variant='body-medium'>
                    Files uploaded successfully! Go to Queue Management to process them.
                  </Typography>
                  <Button
                    size='small'
                    fill='filled'
                    // onClick={() => navigate('/queue-management')} // You'll need to implement routing
                  >
                    Manage Queue <ArrowRightIconOutline className='w-4 h-4 ml-1' />
                  </Button>
                </Flex>
              </div>
            )}
          </div>
        )}

        {/* Information */}
        <div className='mt-4 p-4 bg-blue-50 rounded-lg'>
          <Flex alignItems='center' className='gap-2 mb-2'>
            <InformationCircleIconOutline className='w-5 h-5 text-blue-600' />
            <Typography variant='body-large' className='text-blue-800'>
              How V2 Upload Works
            </Typography>
          </Flex>
          <Typography variant='body-medium' className='text-blue-700'>
            1. Files are uploaded to a queue without processing
            <br />
            2. Use Queue Management to select files and start processing
            <br />
            3. Processing runs in the background and can be monitored
            <br />
            4. Duplicate files are automatically detected and skipped
          </Typography>
        </div>

        {isLoading && <Loader title='Loading...' />}
      </Flex>
    </div>
  );
};

export default DropZoneV2;
