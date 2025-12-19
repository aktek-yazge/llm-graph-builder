import { Route, Routes } from 'react-router-dom';
import ChatOnlyComponent from './components/ChatBot/ChatOnlyComponent';
import ProtectedRoute from './components/Auth/ProtectedRoute';
import LoginPage from './components/Auth/LoginPage';
import Home from './Home';
import { SKIP_AUTH } from './utils/Constants.ts';

const App = () => {
  return (
    <Routes>
      {/* Login route - always accessible */}
      <Route path='/login' element={<LoginPage />} />

      {/* Main route - protected or open based on SKIP_AUTH */}
      <Route
        path='/'
        element={
          SKIP_AUTH ? (
            <Home />
          ) : (
            <ProtectedRoute>
              <Home />
            </ProtectedRoute>
          )
        }
      />

      {/* Readonly mode - always accessible */}
      <Route path='/readonly' element={<Home />} />

      {/* Chat only - always accessible */}
      <Route path='/chat-only' element={<ChatOnlyComponent />} />
    </Routes>
  );
};

export default App;
