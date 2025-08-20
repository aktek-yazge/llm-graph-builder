import { commonserverresponse } from '../types';
import api from '../API/Index';
import { normalizeFileName } from '../utils/utf8';

const retry = async (file: string, retryOption: string) => {
  try {
    const formData = new FormData();
    // Normalize filename before sending to backend
    formData.append('file_name', normalizeFileName(file) || file);
    formData.append('retry_condition', retryOption);
    const response = await api.post<commonserverresponse>(`/retry_processing`, formData);
    return response;
  } catch (error) {
    console.log('Error Posting the Question:', error);
    throw error;
  }
};
export default retry;
