import axios from 'axios';
import { url } from '../utils/Utils';

// Auth API instance
const authApi = axios.create({
  baseURL: url(),
  headers: {
    'Content-Type': 'application/json',
  },
});

// Types
export interface User {
  id: number;
  email: string;
  username: string;
  is_active: boolean;
  created_at: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  username: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface VerifyResponse {
  valid: boolean;
  user_id: number;
  email: string;
  username: string;
  expires: string;
}

// Token storage keys
const TOKEN_KEY = 'jwt_token';
const USER_KEY = 'jwt_user';

// Token management
export const getStoredToken = (): string | null => {
  return localStorage.getItem(TOKEN_KEY);
};

export const getStoredUser = (): User | null => {
  const userStr = localStorage.getItem(USER_KEY);
  if (userStr) {
    try {
      return JSON.parse(userStr);
    } catch {
      return null;
    }
  }
  return null;
};

export const storeAuthData = (token: string, user: User): void => {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
};

export const clearAuthData = (): void => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
};

// Add token to requests
authApi.interceptors.request.use(
  (config) => {
    const token = getStoredToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Handle 401 responses
authApi.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      clearAuthData();
      // Redirect to login if not already there
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

// API calls
export const loginAPI = async (data: LoginRequest): Promise<TokenResponse> => {
  const response = await authApi.post<TokenResponse>('/api/auth/login', data);
  return response.data;
};

export const registerAPI = async (data: RegisterRequest): Promise<TokenResponse> => {
  const response = await authApi.post<TokenResponse>('/api/auth/register', data);
  return response.data;
};

export const verifyTokenAPI = async (): Promise<VerifyResponse> => {
  const response = await authApi.post<VerifyResponse>('/api/auth/verify');
  return response.data;
};

export const logoutAPI = async (): Promise<void> => {
  try {
    await authApi.post('/api/auth/logout');
  } finally {
    clearAuthData();
  }
};

export const getMeAPI = async (): Promise<User> => {
  const response = await authApi.get<User>('/api/auth/me');
  return response.data;
};

// Check if user is authenticated
export const isAuthenticated = (): boolean => {
  const token = getStoredToken();
  return !!token;
};

export default authApi;

