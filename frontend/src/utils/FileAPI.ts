import { Method } from 'axios';
import { apiCall } from '../services/CommonAPI';
import { ExtractParams, UploadParams, UploadV2Params } from '../types';
import { url } from './Utils';
import { normalizeFileName } from './utf8';

// Upload Call
export const uploadAPI = async (
  file: Blob,
  model: string,
  chunkNumber: number,
  totalChunks: number,
  originalname: string,
  generateEmbedding?: boolean
): Promise<any> => {
  const urlUpload = `${url()}/upload`;
  const method: Method = 'post';
  const additionalParams: UploadParams = {
    file,
    model,
    chunkNumber,
    totalChunks,
    originalname: normalizeFileName(originalname) || originalname, // Normalize filename before upload
    generateEmbedding,
  };
  const response = await apiCall(urlUpload, method, additionalParams);
  return response;
};

// Extract call
export const extractAPI = async (
  model: string,
  source_type: string,
  retry_condition: string,
  source_url?: string,
  aws_access_key_id?: string | null,
  aws_secret_access_key?: string | null,
  file_name?: string,
  gcs_bucket_name?: string,
  gcs_bucket_folder?: string,
  allowedNodes?: string[],
  allowedRelationship?: string[],
  token_chunk_size?: number,
  chunk_overlap?: number,
  chunks_to_combine?: number,
  gcs_project_id?: string,
  language?: string,
  access_token?: string,
  additional_instructions?: string,
  enable_post_processing?: boolean,
  post_processing_rules?: string,
  max_pages?: number  // Sayfa sınırlandırma parametresi
): Promise<any> => {
  const urlExtract = `${url()}/extract`;
  const method: Method = 'post';
  let additionalParams: ExtractParams;
  if (source_type === 's3 bucket') {
    additionalParams = {
      model,
      source_url,
      aws_secret_access_key,
      aws_access_key_id,
      source_type,
      file_name,
      allowedNodes,
      allowedRelationship,
      token_chunk_size,
      chunk_overlap,
      chunks_to_combine,
      retry_condition,
      additional_instructions,
      enable_post_processing,
      post_processing_rules,
      max_pages,
    };
  } else if (source_type === 'Wikipedia') {
    additionalParams = {
      model,
      wiki_query: file_name,
      source_type,
      file_name,
      allowedNodes,
      allowedRelationship,
      token_chunk_size,
      chunk_overlap,
      chunks_to_combine,
      language,
      retry_condition,
      additional_instructions,
      enable_post_processing,
      post_processing_rules,
      max_pages,
    };
  } else if (source_type === 'gcs bucket') {
    additionalParams = {
      model,
      gcs_blob_filename: file_name,
      gcs_bucket_folder,
      gcs_bucket_name,
      source_type,
      file_name,
      allowedNodes,
      allowedRelationship,
      token_chunk_size,
      chunk_overlap,
      chunks_to_combine,
      gcs_project_id,
      access_token,
      retry_condition,
      additional_instructions,
      enable_post_processing,
      post_processing_rules,
      max_pages,
    };
  } else if (source_type === 'youtube') {
    additionalParams = {
      model,
      source_url,
      source_type,
      file_name,
      allowedNodes,
      allowedRelationship,
      token_chunk_size,
      chunk_overlap,
      chunks_to_combine,
      retry_condition,
      additional_instructions,
      enable_post_processing,
      post_processing_rules,
      max_pages,
    };
  } else if (source_type === 'web-url') {
    additionalParams = {
      model,
      source_url,
      source_type,
      file_name,
      allowedNodes,
      allowedRelationship,
      token_chunk_size,
      chunk_overlap,
      chunks_to_combine,
      retry_condition,
      additional_instructions,
      enable_post_processing,
      post_processing_rules,
      max_pages,
    };
  } else {
    additionalParams = {
      model,
      source_type,
      file_name,
      allowedNodes,
      allowedRelationship,
      token_chunk_size,
      chunk_overlap,
      chunks_to_combine,
      retry_condition,
      additional_instructions,
      enable_post_processing,
      post_processing_rules,
      max_pages,
    };
  }
  const response = await apiCall(urlExtract, method, additionalParams);
  return response;
};

// ==========================================
// V2 FILE QUEUE API FUNCTIONS
// ==========================================

// V2 Upload API - only uploads file without processing
export const uploadFileToQueueAPI = async (
  file: Blob,
  chunkNumber: number,
  totalChunks: number,
  originalname: string,
  generateEmbedding: boolean = true
): Promise<any> => {
  const urlUpload = `${url()}/api/v2/files/upload`;
  const method: Method = 'post';
  const additionalParams: UploadV2Params = {
    file,
    chunkNumber,
    totalChunks,
    originalname: normalizeFileName(originalname) || originalname,
    generateEmbedding,
  };
  const response = await apiCall(urlUpload, method, additionalParams);
  return response;
};

// Get list of files in queue with optimized hybrid pagination
// Returns all file IDs/statuses but only full details for specified range
export const getQueuedFilesAPI = async (
  detailLimit: number = 100,
  detailOffset: number = 0
): Promise<any> => {
  const urlList = `${url()}/api/v2/files/list?detail_limit=${detailLimit}&detail_offset=${detailOffset}`;
  const method: Method = 'get';
  const response = await apiCall(urlList, method, {});
  return response;
};

// Get details for specific file IDs (for filtered views)
export const getFileDetailsByIdsAPI = async (fileIds: number[]): Promise<any> => {
  if (fileIds.length === 0) {
    return { status: 'Success', data: { files: [] } };
  }
  const urlDetails = `${url()}/api/v2/files/details-by-ids`;
  const method: Method = 'post';
  const response = await apiCall(urlDetails, method, { ids: fileIds.join(',') });
  return response;
};

// Queue file for processing
export const queueFileForProcessingAPI = async (
  fileId: number,
  model: string,
  uri: string,
  userName: string,
  password: string,
  database: string,
  generateEmbedding: boolean = false
): Promise<any> => {
  const urlProcess = `${url()}/api/v2/files/${fileId}/process`;
  const method: Method = 'post';
  const additionalParams = {
    model,
    uri,
    userName,
    password,
    database,
    generateEmbedding: generateEmbedding ? 'true' : 'false',
  };
  const response = await apiCall(urlProcess, method, additionalParams);
  return response;
};

// Get queue status
export const getQueueStatusAPI = async (): Promise<any> => {
  const urlStatus = `${url()}/api/v2/files/status`;
  const method: Method = 'get';
  const response = await apiCall(urlStatus, method, {});
  return response;
};

// Delete file from queue or all files
export const deleteFileFromQueueAPI = async (fileId: number | string): Promise<any> => {
  const urlDelete = `${url()}/api/v2/files/${fileId}`;
  const method: Method = 'delete';
  const response = await apiCall(urlDelete, method, {});
  return response;
};

// Start background processing
export const startBackgroundProcessingAPI = async (): Promise<any> => {
  const urlStart = `${url()}/api/v2/processing/start`;
  const method: Method = 'post';
  const response = await apiCall(urlStart, method, {});
  return response;
};

// Stop background processing
export const stopBackgroundProcessingAPI = async (): Promise<any> => {
  const urlStop = `${url()}/api/v2/processing/stop`;
  const method: Method = 'post';
  const response = await apiCall(urlStop, method, {});
  return response;
};

// Get background processing status
export const getBackgroundProcessingStatusAPI = async (): Promise<any> => {
  const urlStatus = `${url()}/api/v2/processing/status`;
  const method: Method = 'get';
  const response = await apiCall(urlStatus, method, {});
  return response;
};

// Process file immediately
export const processFileImmediatelyAPI = async (fileId: number): Promise<any> => {
  const urlProcess = `${url()}/api/v2/files/${fileId}/process-immediately`;
  const method: Method = 'post';
  const response = await apiCall(urlProcess, method, {});
  return response;
};

// ==========================================
// V2 WORKFLOW STAGE APIs
// ==========================================

// Start chunking for a file or all files
export const startChunkingAPI = async (fileId: number | string): Promise<any> => {
  const urlChunk = `${url()}/api/v2/files/${fileId}/chunk`;
  const method: Method = 'post';
  const response = await apiCall(urlChunk, method, {});
  return response;
};

// Get status of a single V2 file
export const getFileStatusAPI = async (fileId: number): Promise<any> => {
  const urlStatus = `${url()}/api/v2/files/${fileId}/status`;
  const method: Method = 'get';
  const response = await apiCall(urlStatus, method, {});
  return response;
};

// Start graph creation for a file or all files
export const startGraphCreationAPI = async (
  fileId: number | string,
  model: string = 'openai_gpt_4o_mini',
  generateEmbedding: boolean = false
): Promise<any> => {
  const urlGraphCreate = `${url()}/api/v2/files/${fileId}/graph-create`;
  const method: Method = 'post';
  const additionalParams = {
    model,
    generate_embedding: generateEmbedding,
  };
  const response = await apiCall(urlGraphCreate, method, additionalParams);
  return response;
};

// Start graph creation for endorsement files (ENDORSEMENT, RENEWAL, CANCELLATION)
export const startEndorsementGraphCreationAPI = async (
  fileId: number | string = 'all',
  model: string = 'openai_gpt_4o_mini',
  generateEmbedding: boolean = false
): Promise<any> => {
  const urlEndorsementGraphCreate = `${url()}/api/v2/files/endorsements/graph-create`;
  const method: Method = 'post';
  // fileId'yi string'e çevir ve normalize et
  const fileIdStr = fileId === 'all' || fileId === 'ALL' ? 'all' : String(fileId);
  const additionalParams = {
    file_id: fileIdStr,
    model,
    generate_embedding: generateEmbedding ? 'true' : 'false', // Boolean'ı string'e çevir
  };
  console.log('📤 Endorsement graph creation API call:', additionalParams);
  const response = await apiCall(urlEndorsementGraphCreate, method, additionalParams);
  return response;
};

// Start embedding creation for file chunks
// fileId can be: number, "all", or comma-separated IDs like "1,2,3"
export const startEmbeddingAPI = async (
  fileId: number | string
): Promise<any> => {
  const urlEmbedding = `${url()}/api/v2/files/${fileId}/create-embeddings`;
  const method: Method = 'post';
  const response = await apiCall(urlEmbedding, method, {});
  return response;
};

// Reset file to a specific stage
export const resetFileStageAPI = async (
  fileId: number | string,
  stage: 'upload' | 'chunking' | 'graph' | 'auto' | 'invalidate' = 'upload',
  deleteMarkdown: boolean = false
): Promise<any> => {
  const urlReset = `${url()}/api/v2/files/${fileId}/reset?stage=${stage}&delete_markdown=${deleteMarkdown}`;
  const method: Method = 'post';
  const response = await apiCall(urlReset, method, {});
  return response;
};

// Cancel file processing
export const cancelFileProcessingAPI = async (
  fileId: number
): Promise<any> => {
  const urlCancel = `${url()}/api/v2/files/${fileId}/cancel`;
  const method: Method = 'post';
  const response = await apiCall(urlCancel, method, {});
  return response;
};
