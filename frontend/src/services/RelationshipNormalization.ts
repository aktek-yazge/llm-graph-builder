import api from '../API/Index';

export interface RelationshipType {
  type: string;
  count: number;
}

export interface NormalizationGroup {
  suggested_name: string;
  original_types: string[];
  count: number;
  needs_change: boolean;
}

export interface NormalizationPreview {
  groups: NormalizationGroup[];
  total_types: number;
  types_to_merge: number;
  groups_after: number;
}

export interface NormalizationResult {
  success: boolean;
  changes: Array<{
    from: string;
    to: string;
    count: number;
  }>;
  errors: string[];
  total_changed: number;
}

/**
 * Relationship type'larını analiz et ve normalizasyon önizlemesi al
 */
export const getRelationshipNormalizationPreview = async (
  uri: string,
  userName: string,
  password: string,
  database: string
): Promise<NormalizationPreview> => {
  const formData = new FormData();
  formData.append('uri', uri);
  formData.append('userName', userName);
  formData.append('password', password);
  formData.append('database', database);

  const response = await api.post('/relationship_normalization/preview', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });

  if (response.data.status === 'Failed') {
    throw new Error(response.data.message || 'Preview failed');
  }

  return response.data.data;
};

/**
 * Normalizasyonu uygula
 */
export const applyRelationshipNormalization = async (
  uri: string,
  userName: string,
  password: string,
  database: string,
  groups: NormalizationGroup[]
): Promise<NormalizationResult> => {
  const formData = new FormData();
  formData.append('uri', uri);
  formData.append('userName', userName);
  formData.append('password', password);
  formData.append('database', database);
  formData.append('groups', JSON.stringify(groups));

  const response = await api.post('/relationship_normalization/apply', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });

  if (response.data.status === 'Failed') {
    throw new Error(response.data.message || 'Apply failed');
  }

  return response.data.data;
};

/**
 * Mevcut relationship type'larını listele
 */
export const getRelationshipTypes = async (
  uri: string,
  userName: string,
  password: string,
  database: string
): Promise<RelationshipType[]> => {
  const response = await api.get('/relationship_normalization/types', {
    params: { uri, userName, password, database },
  });

  if (response.data.status === 'Failed') {
    throw new Error(response.data.message || 'Failed to get types');
  }

  return response.data.data.types;
};

