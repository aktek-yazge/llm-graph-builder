import { LoadingSpinner } from '@neo4j-ndl/react';
import { DocumentPlusIconSolid } from '@neo4j-ndl/react/icons';
import { useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { v4 as uuidv4 } from 'uuid';
import { useCredentials } from '../../../context/UserCredentials';
import { useFileContext } from '../../../context/UsersFiles';
import { CustomFile } from '../../../types';
import { chunkSize } from '../../../utils/Constants';
import { uploadFileToQueueAPI } from '../../../utils/FileAPI';
import { showErrorToast, showSuccessToast } from '../../../utils/Toasts';
/* eslint-disable no-console */

export default function DropZoneV2ForSmallLayouts() {
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isClicked, setIsClicked] = useState<boolean>(false);
  const { connectionStatus, isReadOnlyUser } = useCredentials();
  const { filesData, setFilesData, model } = useFileContext();
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);

  const uploadFileInChunksV2 = async (file: File) => {
    const totalChunks = Math.ceil(file.size / chunkSize);
    let chunkNumber = 1;
    let start = 0;
    let end = chunkSize;

    console.log(`🚀 V2 Small Layout - Starting upload: ${file.name} (${totalChunks} chunks)`);

    setIsLoading(true);

    try {
      while (chunkNumber <= totalChunks) {
        const chunk = file.slice(start, end);

        console.log(`📤 V2 Uploading chunk ${chunkNumber}/${totalChunks}`);

        const response = await uploadFileToQueueAPI(chunk, chunkNumber, totalChunks, file.name);

        if (response?.status !== 'Success') {
          throw new Error(`Chunk upload failed: ${response?.message || 'Unknown error'}`);
        }

        console.log(`✅ V2 Chunk ${chunkNumber}/${totalChunks} uploaded successfully`);

        // If this is the last chunk, show success
        if (chunkNumber !== totalChunks || !response?.data) {
          chunkNumber++;
          start = end;
          end = Math.min(start + chunkSize, file.size);
          continue;
        }

        const fileInfo = response.data;
        console.log(`🎉 V2 File upload completed: ${file.name} -> ID: ${fileInfo.file_id}`);

        const message = fileInfo.duplicate
          ? `File "${file.name}" already exists in queue`
          : `File "${file.name}" uploaded to queue successfully`;
        showSuccessToast(message);

        // Add to FileContext for FileTable display
        if (!fileInfo.duplicate) {
          const newFile: CustomFile = {
            id: uuidv4(),
            name: fileInfo.original_name,
            type: fileInfo.original_name.substring(fileInfo.original_name.lastIndexOf('.') + 1).toUpperCase(),
            size: 0,
            uploadProgress: 100,
            processingProgress: 0,
            status: 'New',
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
          };
          setFilesData([newFile, ...filesData]);
        }

        chunkNumber++;
        start = end;
        end = Math.min(start + chunkSize, file.size);
      }
    } catch (error: any) {
      console.error(`❌ V2 Small Layout upload failed for ${file.name}:`, error);
      showErrorToast(`Upload failed for "${file.name}": ${error.message}`);
    } finally {
      setIsLoading(false);
      setIsClicked(false);
    }
  };

  const onDropHandler = async (acceptedFiles: File[]) => {
    console.log(`📁 V2 Small Layout - Files dropped: ${acceptedFiles.length} files`);

    if (isReadOnlyUser) {
      showErrorToast('Read-only user cannot upload files');
      return;
    }

    if (!connectionStatus) {
      showErrorToast('Please connect to Neo4j database first');
      return;
    }

    setSelectedFiles(acceptedFiles);

    // Process files sequentially
    for (const file of acceptedFiles) {
      if (!file.size || file.size === 0) {
        showErrorToast(`File "${file.name}" is empty and cannot be uploaded`);
        continue;
      }

      if (file.size > 100 * 1024 * 1024) {
        showErrorToast(`File "${file.name}" is too large. Maximum size is 100MB`);
        continue;
      }

      await uploadFileInChunksV2(file);
    }
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: onDropHandler,
    accept: {
      'application/pdf': ['.pdf'],
      'text/plain': ['.txt'],
      'application/msword': ['.doc'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'text/csv': ['.csv'],
      'application/vnd.ms-excel': ['.xls'],
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
    },
    multiple: true,
    disabled: isLoading || isReadOnlyUser || !connectionStatus,
  });

  const handleClick = () => {
    if (isReadOnlyUser) {
      showErrorToast('Read-only user cannot upload files');
      return;
    }
    if (!connectionStatus) {
      showErrorToast('Please connect to Neo4j database first');
      return;
    }
    setIsClicked(true);
    setTimeout(() => setIsClicked(false), 200);
  };

  return (
    <div
      {...getRootProps()}
      className={`flex justify-center items-center w-10 h-10 rounded-md transition-all duration-200 ${
        isDragActive
          ? 'bg-blue-100 border-2 border-blue-400 border-dashed'
          : isClicked
            ? 'bg-blue-50 scale-95'
            : isLoading
              ? 'bg-gray-100'
              : connectionStatus && !isReadOnlyUser
                ? 'bg-gray-50 hover:bg-blue-50 cursor-pointer'
                : 'bg-gray-100 cursor-not-allowed opacity-50'
      }`}
      onClick={handleClick}
      title={
        isReadOnlyUser
          ? 'Read-only user cannot upload files'
          : !connectionStatus
            ? 'Connect to Neo4j database first'
            : isDragActive
              ? 'Drop files here'
              : 'Upload files to queue (V2)'
      }
    >
      <input {...getInputProps()} />

      {isLoading ? (
        <LoadingSpinner size='small' />
      ) : (
        <DocumentPlusIconSolid
          className={`w-6 h-6 transition-colors ${
            connectionStatus && !isReadOnlyUser && !isLoading
              ? isDragActive
                ? 'text-blue-600'
                : 'text-gray-600 hover:text-blue-600'
              : 'text-gray-400'
          }`}
        />
      )}
    </div>
  );
}
