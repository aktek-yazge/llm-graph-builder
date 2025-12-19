import { createContext, useState, useContext, useEffect, ReactNode, useCallback } from 'react';
import {
  User,
  LoginRequest,
  RegisterRequest,
  TokenResponse,
  loginAPI,
  registerAPI,
  logoutAPI,
  verifyTokenAPI,
  getStoredToken,
  getStoredUser,
  storeAuthData,
  clearAuthData,
} from '../services/AuthAPI';

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (data: LoginRequest) => Promise<boolean>;
  register: (data: RegisterRequest) => Promise<boolean>;
  logout: () => Promise<void>;
  clearError: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider = ({ children }: AuthProviderProps) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Check for existing token on mount
  useEffect(() => {
    const initAuth = async () => {
      const token = getStoredToken();
      const storedUser = getStoredUser();

      if (token && storedUser) {
        try {
          // Verify token is still valid
          await verifyTokenAPI();
          setUser(storedUser);
        } catch (err) {
          // Token invalid, clear storage
          clearAuthData();
          setUser(null);
        }
      }
      setIsLoading(false);
    };

    initAuth();
  }, []);

  const login = useCallback(async (data: LoginRequest): Promise<boolean> => {
    setIsLoading(true);
    setError(null);

    try {
      const response: TokenResponse = await loginAPI(data);
      storeAuthData(response.access_token, response.user);
      setUser(response.user);
      return true;
    } catch (err: any) {
      const errorMessage = err.response?.data?.detail || 'Login failed. Please try again.';
      setError(errorMessage);
      return false;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const register = useCallback(async (data: RegisterRequest): Promise<boolean> => {
    setIsLoading(true);
    setError(null);

    try {
      const response: TokenResponse = await registerAPI(data);
      storeAuthData(response.access_token, response.user);
      setUser(response.user);
      return true;
    } catch (err: any) {
      const errorMessage = err.response?.data?.detail || 'Registration failed. Please try again.';
      setError(errorMessage);
      return false;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const logout = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    try {
      await logoutAPI();
    } catch (err) {
      // Ignore logout errors, just clear local state
    } finally {
      clearAuthData();
      setUser(null);
      setIsLoading(false);
    }
  }, []);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const value: AuthContextType = {
    user,
    isAuthenticated: !!user,
    isLoading,
    error,
    login,
    register,
    logout,
    clearError,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export default AuthProvider;

