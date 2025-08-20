import { commonserverresponse } from '../types';
import api from '../API/Index';
import { normalizeFileName } from '../utils/utf8';

const cancelAPI = async (filenames: string[], source_types: string[]) => {
  try {
    const formData = new FormData();
    // Normalize all filenames before sending to backend
    const normalizedFilenames = filenames.map((name) => normalizeFileName(name) || name);
    formData.append('filenames', JSON.stringify(normalizedFilenames));
    formData.append('source_types', JSON.stringify(source_types));
    const response = await api.post<commonserverresponse>(`/cancelled_job`, formData);
    return response;
  } catch (error) {
    console.log('Error Posting the Question:', error);
    throw error;
  }
};
export default cancelAPI;
