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
      // FormData'ya eklerken tüm değerleri string'e çevir (file hariç)
      if (key === 'file' && value instanceof Blob) {
        formData.append(key, value);
        console.log(`📎 File parameter - Size: ${value.size} bytes, Type: ${value.type}`);
      } else {
        // Boolean, number veya diğer tipleri string'e çevir
        const stringValue = value !== null && value !== undefined ? String(value) : '';
        formData.append(key, stringValue);
        console.log(`📝 Parameter ${key}: ${stringValue} (original: ${value}, type: ${typeof value})`);
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
