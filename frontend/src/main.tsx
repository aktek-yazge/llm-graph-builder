import ReactDOM from 'react-dom/client';
import './index.css';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext.tsx';
import App from './App.tsx';
import { SKIP_AUTH } from './utils/Constants.ts';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    {SKIP_AUTH ? (
      <App />
    ) : (
      <AuthProvider>
        <App />
      </AuthProvider>
    )}
  </BrowserRouter>
);
