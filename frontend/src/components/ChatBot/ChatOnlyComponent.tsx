import { SpotlightProvider } from '@neo4j-ndl/react';
import { useCallback, useEffect, useReducer, useState } from 'react';
import { useLocation } from 'react-router';
import { v4 as uuidv4 } from 'uuid';
import ThemeWrapper from '../../context/ThemeWrapper';
import UserCredentialsWrapper, { useCredentials } from '../../context/UserCredentials';
import { MessageContextWrapper, useMessageContext } from '../../context/UserMessages';
import { FileContextProvider, useFileContext } from '../../context/UsersFiles';
import { clearChatAPI } from '../../services/QnaAPI';
import { ChatProps, connectionState, Messages, UserCredentials } from '../../types';
import { getIsLoading } from '../../utils/Utils';
import Header from '../Layout/Header';
import ConnectionModal from '../Popups/ConnectionModal/ConnectionModal';
import Chatbot from './Chatbot';

const ChatContent: React.FC<ChatProps> = ({ chatMessages }) => {
  const { clearHistoryData, messages, setMessages, setClearHistoryData, setIsDeleteChatLoading, isDeleteChatLoading } =
    useMessageContext();
  const { setUserCredentials, setConnectionStatus, connectionStatus, setShowDisconnectButton } = useCredentials();
  const { model } = useFileContext(); // Model bilgisini almak için
  const [showBackButton, setShowBackButton] = useReducer((state) => !state, false);
  const [openConnection, setOpenConnection] = useState<connectionState>({
    openPopUp: false,
    chunksExists: false,
    vectorIndexMisMatch: false,
    chunksExistsWithDifferentDimension: false,
  });
  /**
   * Initializes connection settings based on URL parameters.
   */
  const initialiseConnection = useCallback(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const uri = urlParams.get('uri');
    const user = urlParams.get('user');
    const encodedPassword = urlParams.get('password');
    const database = urlParams.get('database');
    const port = urlParams.get('port');
    const email = urlParams.get('email');
    const openModal = urlParams.get('open') === 'true';
    const connectionStatus = urlParams.get('connectionStatus') === 'true';
    if (openModal || !(uri && user && encodedPassword && database && port)) {
      if (connectionStatus) {
        setShowBackButton();
        setConnectionStatus(connectionStatus);
        setMessages(chatMessages);
      } else {
        setOpenConnection((prev) => ({ ...prev, openPopUp: true }));
      }
    } else {
      const credentialsForAPI: UserCredentials = {
        uri,
        userName: user,
        password: atob(atob(encodedPassword)),
        database,
        port,
        email: email ?? '',
      };
      setShowBackButton();
      setUserCredentials(credentialsForAPI);
      setConnectionStatus(true);
      setMessages(chatMessages);
      // Remove query params from URL
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }, [chatMessages, setUserCredentials, setConnectionStatus, setMessages]);

  useEffect(() => {
    initialiseConnection();
  }, [initialiseConnection]);
  /**
   * Handles successful connection establishment.
   */
  const handleConnectionSuccess = () => {
    setConnectionStatus(true);
    setShowDisconnectButton(true);
    setOpenConnection((prev) => ({ ...prev, openPopUp: false }));
    const urlParams = new URLSearchParams(window.location.search);
    urlParams.delete('openModal');
    window.history.replaceState({}, document.title, `${window.location.pathname}?${urlParams.toString()}`);
  };
  /**
   * Clears chat history by calling the API.
   */
  const deleteOnClick = async () => {
    try {
      setClearHistoryData(true);
      setIsDeleteChatLoading(true);
      // const credentials = JSON.parse(localStorage.getItem('neo4j.connection') || '{}') as UserCredentials;
      const sessionId = sessionStorage.getItem('session_id') || '';

      // ✅ Yeni session ID'yi önceden oluştur
      const newSessionId = uuidv4();

      const response = await clearChatAPI(sessionId, model, newSessionId);
      setIsDeleteChatLoading(false);
      if (response.data.status === 'Success') {
        // ✅ Yeni session ID'yi sessionStorage'a kaydet
        sessionStorage.setItem('session_id', newSessionId);
        console.log(`🆕 New UUID session ID created after clear chat: ${newSessionId}`);
        console.log(`🤖 New IntelligentAgent created for session: ${newSessionId}, model: ${model}`);
      } else {
        setClearHistoryData(false);
      }
    } catch (error) {
      setIsDeleteChatLoading(false);
      console.error('Error clearing chat history:', error);
      setClearHistoryData(false);
    }
  };
  useEffect(() => {
    if (clearHistoryData) {
      const currentDateTime = new Date();
      setMessages([
        {
          datetime: `${currentDateTime.toLocaleDateString()} ${currentDateTime.toLocaleTimeString()}`,
          id: 2,
          modes: {
            'graph+vector+fulltext': {
              message:
                "Merhaba! Aktek Genai Workbench'e hoş geldiniz. Yüklenen belgelerle ilgili sorular sorabilir ve konuşmalar yapabilirsiniz.",
            },
          },
          user: 'chatbot',
          currentMode: 'graph+vector+fulltext',
        },
      ]);
      setClearHistoryData(false);
    }
  }, [clearHistoryData, setMessages]);
  return (
    <>
      <ConnectionModal
        open={openConnection.openPopUp && !connectionStatus}
        setOpenConnection={setOpenConnection}
        setConnectionStatus={setConnectionStatus}
        isVectorIndexMatch={false}
        chunksExistsWithoutEmbedding={false}
        chunksExistsWithDifferentEmbedding={false}
        onSuccess={handleConnectionSuccess}
        isChatOnly={true}
      />
      <div>
        <Header
          chatOnly={true}
          deleteOnClick={deleteOnClick}
          setOpenConnection={setOpenConnection}
          showBackButton={showBackButton}
        />
        <div>
          <Chatbot
            isFullScreen
            isChatOnly
            messages={messages}
            setMessages={setMessages}
            clear={clearHistoryData}
            isLoading={getIsLoading(messages)}
            connectionStatus={connectionStatus}
            isDeleteChatLoading={isDeleteChatLoading}
          />
        </div>
      </div>
    </>
  );
};
/**
 * ChatOnlyComponent
 * Wrapper component to provide necessary context and initialize chat functionality.
 */
const ChatOnlyComponent: React.FC = () => {
  const location = useLocation();
  const chatMessages = (location.state?.messages as Messages[]) || [];
  return (
    <ThemeWrapper>
      <UserCredentialsWrapper>
        <SpotlightProvider>
          <FileContextProvider>
            <MessageContextWrapper>
              <ChatContent chatMessages={chatMessages} />
            </MessageContextWrapper>
          </FileContextProvider>
        </SpotlightProvider>
      </UserCredentialsWrapper>
    </ThemeWrapper>
  );
};
export default ChatOnlyComponent;
