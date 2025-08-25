import { AxiosResponse, Method } from 'axios';
import { FormDataParams } from '../types';
import api from '../API/Index';

// API Call
const apiCall = async (url: string, method: Method, additionalParams: Partial<FormDataParams>) => {
  try {
    const formData = new FormData();

    console.log(`🌐 Making API call to: ${url} with method: ${method.toUpperCase()}`);
    console.log(`📋 API parameters:`, Object.keys(additionalParams));

    for (const key in additionalParams) {
      const value = additionalParams[key];
      formData.append(key, value);

      // File parametresi için ek bilgi
      if (key === 'file' && value instanceof Blob) {
        console.log(`📎 File parameter - Size: ${value.size} bytes, Type: ${value.type}`);
      } else if (key !== 'file') {
        console.log(`📝 Parameter ${key}: ${value}`);
      }
    }

    const response: AxiosResponse = await api({
      method: method,
      url: url,
      data: formData,
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });

    console.log(`✅ API response received - Status: ${response.status}, Data:`, response.data);
    return response.data;
  } catch (error) {
    console.error('❌ API Error:', error);
    throw error;
  }
};

export { apiCall };
