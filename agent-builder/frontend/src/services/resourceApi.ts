/**
 * Resource API Service
 * ====================
 *
 * Resource management: CRUD, file upload, workspace attachment, status queries.
 */

const RESOURCE_BASE = '/api/v2/resources';

async function resourceRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${RESOURCE_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const err = await response.text();
    throw new Error(err || response.statusText);
  }
  return response.json();
}

// =============================================================================
// TYPES
// =============================================================================

export type ResourceType = 'minio' | 'link' | 'youtube' | 'image' | 'notebooklm';

export interface Resource {
  id: string;
  name: string;
  description?: string;
  type: ResourceType;
  status: 'created' | 'uploading' | 'extracting' | 'ready';
  total_documents: number;
  extracted_documents: number;
  workspace_id?: string;
  metadata?: Record<string, any>;
  created_at?: string;
}

export interface ResourceDocument {
  id: string;
  file_name: string;
  file_type?: string;
  file_size: number;
  page_count: number;
  extraction_status: string;
  processing_status: string;
  confidence_score: number;
  error_message?: string;
  created_at?: string;
}

export interface ResourceDetail extends Resource {
  description: string;
  tenant_id: string;
  minio_bucket: string;
  minio_prefix?: string;
  metadata: Record<string, any>;
  documents: ResourceDocument[];
  status_breakdown: {
    extraction: Record<string, number>;
    processing: Record<string, number>;
  };
  updated_at?: string;
}

export interface UploadResult {
  resource_id: string;
  files_uploaded: number;
  uploaded: Array<{ id: string; file_name: string; size: number }>;
  extraction_tasks: string[];
  message: string;
}

export interface StatusBreakdown {
  resource_id: string;
  status: string;
  total_documents: number;
  extracted_documents: number;
  breakdown: {
    extraction: Record<string, number>;
    processing: Record<string, number>;
  };
}

export interface ExtractedImage {
  name: string;
  path: string;
  size: number;
}

export interface FileTreeDocument {
  id: string;
  file_name: string;
  file_type: string;
  file_size: number;
  page_count: number;
  image_count: number;
  extraction_status: string;
  processing_status: string;
  confidence_score: number;
  error_message?: string;
  extracted_images: ExtractedImage[];
}

export interface FileTree {
  resource_id: string;
  prefix: string;
  documents: FileTreeDocument[];
}

// =============================================================================
// API
// =============================================================================

export const resourceApi = {
  async create(data: {
    name: string;
    type?: ResourceType;
    description?: string;
    tenant_id?: string;
    url?: string;
    notebook_id?: string;
    metadata?: Record<string, any>;
  }): Promise<Resource> {
    return resourceRequest('', {
      method: 'POST',
      body: JSON.stringify({
        name: data.name,
        type: data.type || 'minio',
        description: data.description || '',
        tenant_id: data.tenant_id || 'default',
        url: data.url || null,
        notebook_id: data.notebook_id || null,
        metadata: data.metadata || {},
      }),
    });
  },

  async list(tenantId = 'default', limit = 50, type?: ResourceType): Promise<{ resources: Resource[]; count: number }> {
    let qs = `?tenant_id=${tenantId}&limit=${limit}`;
    if (type) qs += `&type=${type}`;
    return resourceRequest(qs);
  },

  async getDetail(id: string, includeDocs = true): Promise<ResourceDetail> {
    return resourceRequest(`/${id}?include_docs=${includeDocs}`);
  },

  async getStatus(id: string): Promise<StatusBreakdown> {
    return resourceRequest(`/${id}/status`);
  },

  async listFiles(id: string): Promise<FileTree> {
    return resourceRequest(`/${id}/files`);
  },

  async getPreviewUrl(resourceId: string, path: string): Promise<{ url: string }> {
    return resourceRequest(`/${resourceId}/preview?path=${encodeURIComponent(path)}`);
  },

  async upload(
    id: string,
    files: File[],
    onProgress?: (pct: number) => void,
  ): Promise<UploadResult> {
    const formData = new FormData();
    files.forEach((f) => formData.append('files', f));

    const xhr = new XMLHttpRequest();

    return new Promise((resolve, reject) => {
      xhr.open('POST', `${RESOURCE_BASE}/${id}/upload`);

      if (onProgress) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
        };
      }

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          reject(new Error(xhr.statusText));
        }
      };

      xhr.onerror = () => reject(new Error('Upload failed'));
      xhr.send(formData);
    });
  },

  async attachToWorkspace(resourceId: string, workspaceId: string): Promise<{ message: string }> {
    return resourceRequest(`/${resourceId}/attach/${workspaceId}`, { method: 'POST' });
  },

  async remove(id: string): Promise<{ message: string }> {
    return resourceRequest(`/${id}`, { method: 'DELETE' });
  },
};

export default resourceApi;
