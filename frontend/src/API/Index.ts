import axios from 'axios';
import { url } from '../utils/Utils';
import { UserCredentials } from '../types';

const api = axios.create({
  baseURL: url(),
  data: {},
});

export const createDefaultFormData = (userCredentials: UserCredentials) => {
  const formData = new FormData();
  if (userCredentials?.uri) {
    formData.append('uri', userCredentials?.uri);
  }
  if (userCredentials?.database) {
    formData.append('database', userCredentials?.database);
  }
  if (userCredentials?.userName) {
    formData.append('userName', userCredentials?.userName);
  }
  if (userCredentials?.password) {
    formData.append('password', userCredentials?.password);
  }
  if (userCredentials?.email) {
    formData.append('email', userCredentials?.email);
  }
  api.interceptors.request.use(
    (config) => {
      if (config.data instanceof FormData) {
        for (const [key, value] of formData.entries()) {
          if (!config.data.has(key)) {
            config.data.append(key, value);
          }
        }
      } else {
        const formData = new FormData();
        for (const [key, value] of formData.entries()) {
          formData.append(key, value);
        }
        for (const [key, value] of Object.entries(config.data || {})) {
          formData.append(key, value as any);
        }
        config.data = formData;
      }

      return config;
    },
    (error) => {
      return Promise.reject(error);
    }
  );
  return formData;
};

export default api;

// QA Based Extraction API functions
export const extractQABased = (data: {
  document_chunks: string[];
  file_name: string;
  model: string;
  domain?: string;
  custom_questions?: any;
  uri?: string;
  userName?: string;
  password?: string;
  database?: string;
  email?: string;
}) => {
  const formData = new FormData();
  formData.append('document_chunks', JSON.stringify(data.document_chunks));
  formData.append('file_name', data.file_name);
  formData.append('model', data.model);

  if (data.domain) {
    formData.append('domain', data.domain);
  }
  if (data.custom_questions) {
    formData.append('custom_questions', JSON.stringify(data.custom_questions));
  }
  if (data.uri) {
    formData.append('uri', data.uri);
  }
  if (data.userName) {
    formData.append('userName', data.userName);
  }
  if (data.password) {
    formData.append('password', data.password);
  }
  if (data.database) {
    formData.append('database', data.database);
  }
  if (data.email) {
    formData.append('email', data.email);
  }

  return api.post('/extract_qa_based', formData);
};

export const loadQASchema = (schemaFile: string) => {
  const formData = new FormData();
  formData.append('schema_file', schemaFile);
  return api.post('/load_qa_schema', formData);
};

export const listQASchemas = () => {
  return api.get('/list_qa_schemas');
};

export const convertToMarkdown = (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post('/convert-to-markdown', formData);
};
