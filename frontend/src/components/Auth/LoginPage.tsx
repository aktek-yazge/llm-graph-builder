import { useState, FormEvent, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Button, TextInput, Typography, Flex, Banner } from '@neo4j-ndl/react';
import { useAuth } from '../../context/AuthContext';

type AuthMode = 'login' | 'register';

const LoginPage = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, register, isAuthenticated, isLoading, error, clearError } = useAuth();

  const [mode, setMode] = useState<AuthMode>('login');
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);

  // Redirect if already authenticated
  useEffect(() => {
    if (isAuthenticated) {
      const from = (location.state as any)?.from?.pathname || '/';
      navigate(from, { replace: true });
    }
  }, [isAuthenticated, navigate, location]);

  // Clear errors when switching modes
  useEffect(() => {
    clearError();
    setValidationError(null);
  }, [mode, clearError]);

  const validateForm = (): boolean => {
    if (!email || !password) {
      setValidationError('Email and password are required');
      return false;
    }

    if (!email.includes('@')) {
      setValidationError('Please enter a valid email address');
      return false;
    }

    if (password.length < 6) {
      setValidationError('Password must be at least 6 characters');
      return false;
    }

    if (mode === 'register') {
      if (!username || username.length < 3) {
        setValidationError('Username must be at least 3 characters');
        return false;
      }

      if (password !== confirmPassword) {
        setValidationError('Passwords do not match');
        return false;
      }
    }

    setValidationError(null);
    return true;
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();

    if (!validateForm()) return;

    let success: boolean;

    if (mode === 'login') {
      success = await login({ email, password });
    } else {
      success = await register({ email, username, password });
    }

    if (success) {
      const from = (location.state as any)?.from?.pathname || '/';
      navigate(from, { replace: true });
    }
  };

  const toggleMode = () => {
    setMode(mode === 'login' ? 'register' : 'login');
    setPassword('');
    setConfirmPassword('');
  };

  const displayError = validationError || error;

  return (
    <div className='min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900'>
      {/* Background pattern */}
      <div className='absolute inset-0 bg-[url("data:image/svg+xml,%3Csvg width="60" height="60" viewBox="0 0 60 60" xmlns="http://www.w3.org/2000/svg"%3E%3Cg fill="none" fill-rule="evenodd"%3E%3Cg fill="%239C92AC" fill-opacity="0.05"%3E%3Cpath d="M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z"/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")] opacity-40'></div>

      <div className='relative z-10 w-full max-w-md px-6'>
        {/* Logo/Brand */}
        <div className='text-center mb-8'>
          <div className='inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-tr from-blue-500 to-purple-600 mb-4 shadow-lg shadow-purple-500/30'>
            <svg className='w-8 h-8 text-white' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
              <path
                strokeLinecap='round'
                strokeLinejoin='round'
                strokeWidth={2}
                d='M13 10V3L4 14h7v7l9-11h-7z'
              />
            </svg>
          </div>
          <Typography variant='h2' className='text-white font-bold'>
            LLM Graph Builder
          </Typography>
          <Typography variant='body-medium' className='text-gray-400 mt-2'>
            {mode === 'login' ? 'Welcome back! Please sign in.' : 'Create your account to get started.'}
          </Typography>
        </div>

        {/* Card */}
        <div className='bg-white/10 backdrop-blur-xl rounded-2xl shadow-2xl border border-white/20 p-8'>
          {displayError && (
            <Banner
              type='danger'
              title='Error'
              description={displayError}
              className='mb-6'
              closeable
              onClose={() => {
                clearError();
                setValidationError(null);
              }}
            />
          )}

          <form onSubmit={handleSubmit} className='space-y-5'>
            <div>
              <label className='block text-sm font-medium text-gray-200 mb-2'>Email</label>
              <TextInput
                type='email'
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder='you@example.com'
                fluid
                disabled={isLoading}
                className='bg-white/5 border-white/10 text-white placeholder-gray-500'
              />
            </div>

            {mode === 'register' && (
              <div>
                <label className='block text-sm font-medium text-gray-200 mb-2'>Username</label>
                <TextInput
                  type='text'
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder='johndoe'
                  fluid
                  disabled={isLoading}
                  className='bg-white/5 border-white/10 text-white placeholder-gray-500'
                />
              </div>
            )}

            <div>
              <label className='block text-sm font-medium text-gray-200 mb-2'>Password</label>
              <TextInput
                type='password'
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder='••••••••'
                fluid
                disabled={isLoading}
                className='bg-white/5 border-white/10 text-white placeholder-gray-500'
              />
            </div>

            {mode === 'register' && (
              <div>
                <label className='block text-sm font-medium text-gray-200 mb-2'>Confirm Password</label>
                <TextInput
                  type='password'
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder='••••••••'
                  fluid
                  disabled={isLoading}
                  className='bg-white/5 border-white/10 text-white placeholder-gray-500'
                />
              </div>
            )}

            <Button
              type='submit'
              size='large'
              loading={isLoading}
              disabled={isLoading}
              className='w-full bg-gradient-to-r from-blue-500 to-purple-600 hover:from-blue-600 hover:to-purple-700 border-0 shadow-lg shadow-purple-500/25'
            >
              {mode === 'login' ? 'Sign In' : 'Create Account'}
            </Button>
          </form>

          <div className='mt-6 text-center'>
            <Typography variant='body-small' className='text-gray-400'>
              {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
              <button
                type='button'
                onClick={toggleMode}
                className='text-purple-400 hover:text-purple-300 font-medium transition-colors'
                disabled={isLoading}
              >
                {mode === 'login' ? 'Sign up' : 'Sign in'}
              </button>
            </Typography>
          </div>
        </div>

        {/* Footer */}
        <div className='mt-8 text-center'>
          <Typography variant='body-small' className='text-gray-500'>
            Powered by Neo4j & LLM Technology
          </Typography>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;

