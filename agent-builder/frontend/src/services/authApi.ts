import axios from 'axios';

const agentBuilderUrl = (): string => {
  if (import.meta.env.VITE_AGENT_BUILDER_URL) {
    const u = import.meta.env.VITE_AGENT_BUILDER_URL as string;
    return u.endsWith('/') ? u.slice(0, -1) : u;
  }
  return '';
};

const authApi = axios.create({ baseURL: agentBuilderUrl() });
const AUTH_PREFIX = '/api/v2/auth';

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user_id: string;
  tenant_id: string;
  role: string;
}

export interface UserInfo {
  user_id: string;
  tenant_id: string;
  tenant_name: string;
  email: string;
  full_name: string;
  role: string;
}

export const registerUser = (data: {
  tenant_name: string;
  email: string;
  password: string;
  full_name: string;
}) => authApi.post<TokenResponse>(`${AUTH_PREFIX}/register`, data);

export const loginUser = (data: { email: string; password: string }) =>
  authApi.post<TokenResponse>(`${AUTH_PREFIX}/login`, data);

export const refreshToken = (refresh_token: string) =>
  authApi.post<TokenResponse>(`${AUTH_PREFIX}/refresh`, { refresh_token });

export const getMe = (accessToken: string) =>
  authApi.get<UserInfo>(`${AUTH_PREFIX}/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
