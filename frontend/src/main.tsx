import ReactDOM from 'react-dom/client';
import './index.css';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext.tsx';
import App from './App.tsx';
import { SKIP_AUTH } from './utils/Constants.ts';

// Vite base URL'den basename al (örn: '/wat/' -> '/wat')
const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <BrowserRouter basename={basename}>
    {SKIP_AUTH ? (
      <App />
    ) : (
      <AuthProvider>
        <App />
      </AuthProvider>
    )}
  </BrowserRouter>
);
