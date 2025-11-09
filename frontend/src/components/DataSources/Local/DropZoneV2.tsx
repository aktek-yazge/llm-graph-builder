/* eslint-disable no-console */
import { Dropzone, Flex, SpotlightTarget, Typography } from '@neo4j-ndl/react';
import { InformationCircleIconOutline } from '@neo4j-ndl/react/icons';
import { FunctionComponent, useCallback, useEffect, useState } from 'react';
import { useCredentials } from '../../../context/UserCredentials';
import { useFileContext } from '../../../context/UsersFiles';
import { CustomFile } from '../../../types';
import { buttonCaptions, chunkSize } from '../../../utils/Constants';
import { uploadFileToQueueAPI } from '../../../utils/FileAPI';
import Loader from '../../../utils/Loader';
import { showErrorToast, showSuccessToast } from '../../../utils/Toasts';
import { normalizeFileName } from '../../../utils/utf8';
import { IconButtonWithToolTip } from '../../UI/IconButtonToolTip';

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

        const response = await uploadFileToQueueAPI(chunk, chunkNumber, totalChunks, file.name, true);

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
    <>
      <SpotlightTarget
        id='dropzone-v2'
        hasPulse={true}
        indicatorVariant='border'
        hasAnchorPortal={false}
        borderRadius={11}
      >
        <Dropzone
          loadingComponent={
            (isLoading || isUploading) && (
              <Loader
                title={
                  isUploading
                    ? `V2 Batch Upload: ${batchProgress.current}/${batchProgress.total} files`
                    : 'Uploading V2'
                }
              />
            )
          }
          isTesting={true}
          className='bg-none! dropzoneContainer'
          supportedFilesDescription={
            <Typography variant='body-small'>
              <Flex>
                <span>{buttonCaptions.dropzoneSpan}</span>
                <div className='align-self-center'>
                  <IconButtonWithToolTip
                    label='V2 Queue Source info'
                    clean
                    text={
                      <Typography variant='body-small'>
                        <Flex gap='3' alignItems='flex-start'>
                          <span>Microsoft Office (.docx, .xlsx)</span>
                          <span>PDF (.pdf)</span>
                          <span>Text (.txt, .csv)</span>
                          <span>V2 Queue System - Upload to process later</span>
                        </Flex>
                      </Typography>
                    }
                  >
                    <InformationCircleIconOutline className='w-[22px] h-[22px]' />
                  </IconButtonWithToolTip>
                </div>
              </Flex>
            </Typography>
          }
          dropZoneOptions={{
            accept: {
              'application/pdf': ['.pdf'],
              'text/plain': ['.txt'],
              'application/msword': ['.doc'],
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
              'text/csv': ['.csv'],
              'application/vnd.ms-excel': ['.xls'],
              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
            },
            onDrop: (f: Partial<globalThis.File>[]) => {
              onDropHandler(f);
            },
            onDropRejected: (e) => {
              if (e.length) {
                showErrorToast('Failed To Upload, Unsupported file extension');
              }
            },
            multiple: true,
            disabled: isUploading,
          }}
        />
      </SpotlightTarget>
    </>
  );
};

export default DropZoneV2;
