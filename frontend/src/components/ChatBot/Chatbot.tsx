import {
  Avatar,
  Box,
  Flex,
  IconButton,
  Modal,
  SpotlightTarget,
  TextInput,
  TextLink,
  Typography,
  useCopyToClipboard,
  Widget,
} from '@neo4j-ndl/react';
import { ArrowDownTrayIconOutline, XMarkIconOutline } from '@neo4j-ndl/react/icons';
import clsx from 'clsx';
import React, { FC, lazy, Suspense, useCallback, useEffect, useReducer, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import { v4 as uuidv4 } from 'uuid';
import ChatBotAvatar from '../../assets/images/chatbot-ai.png';
import { useFileContext } from '../../context/UsersFiles';
import { useCredentials } from '../../context/UserCredentials';
import { useAuth } from '../../context/AuthContext';
import { SKIP_AUTH } from '../../utils/Constants';
import useSpeechSynthesis from '../../hooks/useSpeech';
import { chatStreamAPI, ChatStreamMessage } from '../../services/ChatStreamAPI';
import { chatBotAPI } from '../../services/QnaAPI';
import {
  ChatbotProps,
  Chunk,
  Community,
  CustomFile,
  Entity,
  ExtendedNode,
  ExtendedRelationship,
  Messages,
  metricstate,
  multimodelmetric,
  nodeDetailsProps,
  ResponseMode,
} from '../../types';
import { buttonCaptions, chatModeLables } from '../../utils/Constants';
import Loader from '../../utils/Loader';
import { downloadClickHandler, getDateTime } from '../../utils/Utils';
import ButtonWithToolTip from '../UI/ButtonWithToolTip';
import FallBackDialog from '../UI/FallBackDialog';
import ChatModesSwitch from './ChatModesSwitch';
import CommonActions from './CommonChatActions';
const InfoModal = lazy(() => import('./ChatInfoModal'));

// Session ID'yi initialize et - veritabanında mevcut session varsa yeni ID oluştur
const initializeSessionId = () => {
  if (typeof window !== 'undefined') {
    let sessionId = sessionStorage.getItem('session_id');

    // Eğer session ID yoksa veya çok eskiyse yeni oluştur
    if (!sessionId) {
      sessionId = uuidv4();
      sessionStorage.setItem('session_id', sessionId);
      console.log(`🆕 New session ID created: ${sessionId}`);
    } else {
      console.log(`♻️ Existing session ID used: ${sessionId}`);
    }

    return sessionId;
  }
  return '';
};

const Chatbot: FC<ChatbotProps> = (props) => {
  const {
    messages: listMessages,
    setMessages: setListMessages,
    isLoading,
    isFullScreen,
    connectionStatus,
    isChatOnly,
    isDeleteChatLoading,
  } = props;

  // ⚠️ FIX: Session ID'yi state olarak yönet (clear chat'ten sonra güncellenmesi için)
  const [sessionId, setSessionId] = useState<string>(() => initializeSessionId());

  const [inputMessage, setInputMessage] = useState('');
  const [loading, setLoading] = useState<boolean>(isLoading);
  const { model, chatModes, selectedRows, filesData } = useFileContext();
  const { userCredentials } = useCredentials();
  
  // JWT Auth - kullanıcı email'i için
  const auth = SKIP_AUTH ? null : useAuth();
  const authUser = auth?.user;
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [showInfoModal, setShowInfoModal] = useState<boolean>(false);
  const [sourcesModal, setSourcesModal] = useState<string[]>([]);
  const [modelModal, setModelModal] = useState<string>('');
  const [responseTime, setResponseTime] = useState<number>(0);
  const [tokensUsed, setTokensUsed] = useState<number>(0);
  const [agentInputTokens, setAgentInputTokens] = useState<number>(0);
  const [agentOutputTokens, setAgentOutputTokens] = useState<number>(0);
  const [agentTotalTokens, setAgentTotalTokens] = useState<number>(0);
  const [agentChunkDetails, setAgentChunkDetails] = useState<
    Array<{
      document: string;
      page: number;
      relevance: number;
      preview: string;
    }>
  >([]);
  const [agentEntityDetails, setAgentEntityDetails] = useState<
    Array<{
      id: string;
      type: string;
      labels: string[];
    }>
  >([]);
  const [agentDiscoveredEntities, setAgentDiscoveredEntities] = useState<number>(0);
  const [agentDiscoveredChunks, setAgentDiscoveredChunks] = useState<number>(0);
  const [agentIterations, setAgentIterations] = useState<number>(0);
  const [cypherQuery, setcypherQuery] = useState<string>('');
  const [chatsMode, setChatsMode] = useState<string>(chatModeLables['graph+vector+fulltext']);
  const [graphEntitites, setgraphEntitites] = useState<any[]>([]);
  const [messageError, setmessageError] = useState<string>('');
  const [entitiesModal, setEntitiesModal] = useState<string[]>([]);
  const [nodeDetailsModal, setNodeDetailsModal] = useState<nodeDetailsProps>({});
  const [metricQuestion, setMetricQuestion] = useState<string>('');
  const [metricAnswer, setMetricAnswer] = useState<string>('');
  const [metricContext, setMetricContext] = useState<string>('');
  const [nodes, setNodes] = useState<ExtendedNode[]>([]);
  const [relationships, setRelationships] = useState<ExtendedRelationship[]>([]);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [metricDetails, setMetricDetails] = useState<metricstate | null>(null);
  const [infoEntities, setInfoEntities] = useState<Entity[]>([]);
  const [communities, setCommunities] = useState<Community[]>([]);
  const [infoLoading, toggleInfoLoading] = useReducer((s) => !s, false);
  const [metricsLoading, toggleMetricsLoading] = useReducer((s) => !s, false);
  const downloadLinkRef = useRef<HTMLAnchorElement>(null);
  const [activeChat, setActiveChat] = useState<Messages | null>(null);
  const [multiModelMetrics, setMultiModelMetrics] = useState<multimodelmetric[]>([]);
  const [isStreamingEnabled, setIsStreamingEnabled] = useState<boolean>(false);
  
  // Thinking steps for streaming - düşünce süreçlerini göstermek için
  const [thinkingSteps, setThinkingSteps] = useState<string[]>([]);

  // ⚠️ FIX: Session ID değişikliklerini dinle (clear chat'ten sonra güncellenmesi için)
  useEffect(() => {
    const handleStorageChange = () => {
      const newSessionId = sessionStorage.getItem('session_id') ?? '';
      if (newSessionId !== sessionId) {
        setSessionId(newSessionId);
        console.log(`🔄 Session ID updated from storage: ${newSessionId}`);
      }
    };

    // Storage change event'ini dinle
    window.addEventListener('storage', handleStorageChange);

    // Interval ile de kontrol et (aynı tab içindeki değişiklikler için)
    const interval = setInterval(() => {
      const currentSessionId = sessionStorage.getItem('session_id') ?? '';
      if (currentSessionId !== sessionId) {
        setSessionId(currentSessionId);
        console.log(`🔄 Session ID updated via interval: ${currentSessionId}`);
      }
    }, 1000);

    return () => {
      window.removeEventListener('storage', handleStorageChange);
      clearInterval(interval);
    };
  }, [sessionId]);

  const [_, copy] = useCopyToClipboard();
  const { speak, cancel, speaking } = useSpeechSynthesis({
    onEnd: () => {
      setListMessages((msgs) => msgs.map((msg) => ({ ...msg, speaking: false })));
    },
  });

  let selectedFileNames: CustomFile[] = filesData.filter(
    (f) => selectedRows.includes(f.id) && ['Completed'].includes(f.status)
  );

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInputMessage(e.target.value);
  };

  const saveInfoEntitites = (entities: Entity[]) => {
    setInfoEntities(entities);
  };

  const saveNodes = (chatNodes: ExtendedNode[]) => {
    setNodes(chatNodes);
  };

  const saveChatRelationships = (chatRels: ExtendedRelationship[]) => {
    setRelationships(chatRels);
  };

  const saveChunks = (chatChunks: Chunk[]) => {
    setChunks(chatChunks);
  };
  const saveMultimodemetrics = (metrics: multimodelmetric[]) => {
    setMultiModelMetrics(metrics);
  };
  const saveMetrics = (metricInfo: metricstate) => {
    setMetricDetails(metricInfo);
  };
  const saveCommunities = (chatCommunities: Community[]) => {
    setCommunities(chatCommunities);
  };

  const simulateTypingEffect = (messageId: number, response: ResponseMode, mode: string, message: string) => {
    let index = 0;
    let lastTimestamp: number | null = null;
    const TYPING_INTERVAL = 20;
    const animate = (timestamp: number) => {
      if (lastTimestamp === null) {
        lastTimestamp = timestamp;
      }
      const elapsed = timestamp - lastTimestamp;
      if (elapsed >= TYPING_INTERVAL) {
        if (index < message.length) {
          const nextIndex = index + 1;
          const currentTypedText = message.substring(0, nextIndex);
          setListMessages((msgs) =>
            msgs.map((msg) => {
              if (msg.id === messageId) {
                return {
                  ...msg,
                  modes: {
                    ...msg.modes,
                    [mode]: {
                      ...response,
                      message: currentTypedText,
                    },
                  },
                  isTyping: true,
                  speaking: false,
                  copying: false,
                };
              }
              return msg;
            })
          );
          index = nextIndex;
          lastTimestamp = timestamp;
        } else {
          setListMessages((msgs) => {
            const activeMessage = msgs.find((message) => message.id === messageId);
            let sortedModes: Record<string, ResponseMode>;
            if (activeMessage) {
              sortedModes = Object.fromEntries(
                chatModes.filter((m) => m in activeMessage.modes).map((key) => [key, activeMessage?.modes[key]])
              );
            }
            return msgs.map((msg) => (msg.id === messageId ? { ...msg, isTyping: false, modes: sortedModes } : msg));
          });
          return;
        }
      }
      requestAnimationFrame(animate);
    };
    requestAnimationFrame(animate);
  };

  const handleStreamingSubmit = async (inputMessage: string) => {
    const datetime = getDateTime();
    // Generate unique question_id for log correlation
    const questionId = uuidv4();
    console.log(`📝 Question ID: ${questionId} | Session: ${sessionId}`);
    
    const userMessage: Messages = {
      id: Date.now(),
      user: 'user',
      datetime: datetime,
      currentMode: chatModes[0],
      modes: {},
    };
    userMessage.modes[chatModes[0]] = { message: inputMessage };
    setListMessages([...listMessages, userMessage]);

    const chatbotMessageId = Date.now() + 1;
    const chatbotMessage: Messages = {
      id: chatbotMessageId,
      user: 'chatbot',
      datetime: new Date().toLocaleString(),
      isTyping: true,
      isLoading: true,
      modes: {},
      currentMode: chatModes[0],
    };
    setListMessages((prev) => [...prev, chatbotMessage]);

    try {
      // Langfuse User Tracking için user_id - öncelik: authUser > userCredentials > fallback
      const userId = authUser?.email || userCredentials?.email || userCredentials?.userName || `user_${sessionId.slice(0, 8)}`;
      console.log('🔍 Sending chat request with user_id:', userId, '(authUser:', authUser?.email, ')');
      
      await chatStreamAPI.startChatStream(
        {
          question: inputMessage,
          session_id: sessionId,
          question_id: questionId,  // Log correlation ID
          model,
          mode: chatModes[0],
          document_names: selectedFileNames?.map((f) => f.name),
          user_id: userId,  // Langfuse User Tracking için
        },
        (message: ChatStreamMessage) => {
          // eslint-disable-next-line no-console
          console.log('Frontend received message:', message);

          if (message.type === 'status') {
            // Status mesajlarını handle et (başlangıç durumları vs.)
            // eslint-disable-next-line no-console
            console.log('Status:', message.message);
          } else if (message.type === 'thinking_step') {
            // Düşünce adımını ekle - kullanıcıya göster
            const thinkingMessage = message.message || '';
            if (thinkingMessage) {
              setThinkingSteps((prev) => [...prev, thinkingMessage]);
            }
          } else if (message.type === 'message_chunk' && (message.content || message.full_message)) {
            // Sadece is_final_answer true ise thinking steps'i temizle
            // Böylece tool çağrıları sırasındaki ara mesajlar thinking steps'i bozmaz
            if (message.is_final_answer) {
              setThinkingSteps([]);
            }
            // Kelime kelime streaming - full_message varsa onu kullan, yoksa content'i ekle
            setListMessages((prev) =>
              prev.map((msg) => {
                if (msg.id === chatbotMessageId) {
                  const currentResponse = msg.modes[chatModes[0]] || { message: '' };

                  // full_message varsa bunu kullan (daha güvenilir)
                  // Yoksa mevcut mesaja content'i ekle
                  const newMessage = message.full_message || currentResponse.message + (message.content || '');

                  return {
                    ...msg,
                    modes: {
                      ...msg.modes,
                      [chatModes[0]]: {
                        ...currentResponse,
                        message: newMessage,
                      },
                    },
                    isTyping: true,
                    isLoading: !message.is_complete,
                  };
                }
                return msg;
              })
            );

            // Eğer bu son chunk ise, typing'i durdur
            if (message.is_complete) {
              setTimeout(() => {
                setListMessages((prev) =>
                  prev.map((msg) => {
                    if (msg.id === chatbotMessageId) {
                      return {
                        ...msg,
                        isTyping: false,
                        isLoading: false,
                      };
                    }
                    return msg;
                  })
                );
              }, 100);
            }
          } else if (message.type === 'complete') {
            // Final response - thinking steps'i temizle
            setThinkingSteps([]);
            
            // Final response ile tüm bilgileri güncelle - message direkt seviyede gelir
            const responseMode: ResponseMode = {
              message: message.message || '',
              sources: message.info?.sources || [],
              model: message.info?.model || '',
              total_tokens: message.info?.total_tokens || 0,
              agent_input_tokens: message.info?.agent_input_tokens || 0,
              agent_output_tokens: message.info?.agent_output_tokens || 0,
              agent_total_tokens: message.info?.agent_total_tokens || 0,
              agent_chunk_details: message.info?.agent_chunk_details || [],
              agent_entity_details: message.info?.agent_entity_details || [],
              agent_discovered_entities: message.info?.agent_discovered_entities || 0,
              agent_discovered_chunks: message.info?.agent_discovered_chunks || 0,
              agent_iterations: message.info?.agent_iterations || 0,
              response_time: message.info?.response_time || 0,
              cypher_query: message.info?.cypher_query || '',
              graphonly_entities: message.info?.context || [],
              entities: message.info?.entities?.entityids || [],
              nodeDetails: message.info?.nodedetails || {},
              error: message.info?.error || '',
              metric_question: message.info?.metric_details?.question || '',
              metric_answer: message.info?.metric_details?.answer || '',
              metric_contexts: message.info?.metric_details?.contexts || '',
              personPolicyInfo: message.info?.personPolicyInfo || [], // YENI: PersonPolicyInfo ekle
            };

            setListMessages((prev) =>
              prev.map((msg) => {
                if (msg.id === chatbotMessageId) {
                  return {
                    ...msg,
                    modes: { ...msg.modes, [chatModes[0]]: responseMode },
                    isTyping: false,
                    isLoading: false,
                  };
                }
                return msg;
              })
            );
          } else if (message.type === 'error') {
            setListMessages((prev) =>
              prev.map((msg) => {
                if (msg.id === chatbotMessageId) {
                  return {
                    ...msg,
                    modes: {
                      ...msg.modes,
                      [chatModes[0]]: {
                        message: message.message || 'Bir hata oluştu',
                        error: message.error || 'Unknown error',
                      },
                    },
                    isTyping: false,
                    isLoading: false,
                  };
                }
                return msg;
              })
            );
          }
        },
        (error: Error) => {
          // eslint-disable-next-line no-console
          console.error('Streaming error:', error);
          setListMessages((prev) =>
            prev.map((msg) => {
              if (msg.id === chatbotMessageId) {
                return {
                  ...msg,
                  modes: {
                    ...msg.modes,
                    [chatModes[0]]: {
                      message: 'Bağlantı hatası oluştu',
                      error: error.message,
                    },
                  },
                  isTyping: false,
                  isLoading: false,
                };
              }
              return msg;
            })
          );
        },
        () => {
          // Stream tamamlandı
          setListMessages((prev) =>
            prev.map((msg) => {
              if (msg.id === chatbotMessageId) {
                return { ...msg, isTyping: false, isLoading: false };
              }
              return msg;
            })
          );
        }
      );
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('Error in streaming chat:', error);
      setListMessages((prev) =>
        prev.map((msg) => {
          if (msg.id === chatbotMessageId) {
            return {
              ...msg,
              modes: {
                ...msg.modes,
                [chatModes[0]]: {
                  message: 'Bir hata oluştu',
                  error: error instanceof Error ? error.message : 'Unknown error',
                },
              },
              isTyping: false,
              isLoading: false,
            };
          }
          return msg;
        })
      );
    }
  };

  const handleSubmit = async (e: { preventDefault: () => void }) => {
    e.preventDefault();
    if (!inputMessage.trim()) {
      return;
    }

    // Stream modu aktifse streaming kullan
    if (isStreamingEnabled) {
      await handleStreamingSubmit(inputMessage);
      setInputMessage('');
      return;
    }

    // Normal mode (mevcut kod)
    const datetime = getDateTime();
    // Generate unique question_id for log correlation
    const questionId = uuidv4();
    console.log(`📝 Question ID: ${questionId} | Session: ${sessionId}`);
    
    const userMessage: Messages = {
      id: Date.now(),
      user: 'user',
      datetime: datetime,
      currentMode: chatModes[0],
      modes: {},
    };
    userMessage.modes[chatModes[0]] = { message: inputMessage };
    setListMessages([...listMessages, userMessage]);
    const chatbotMessageId = Date.now() + 1;
    const chatbotMessage: Messages = {
      id: chatbotMessageId,
      user: 'chatbot',
      datetime: new Date().toLocaleString(),
      isTyping: true,
      isLoading: true,
      modes: {},
      currentMode: chatModes[0],
    };
    setListMessages((prev) => [...prev, chatbotMessage]);
    try {
      const apiCalls = chatModes.map((mode) =>
        chatBotAPI(
          inputMessage,
          sessionId,
          model,
          mode,
          selectedFileNames?.map((f) => f.name),
          questionId  // Log correlation ID - same for all modes
        )
      );
      setInputMessage('');
      const results = await Promise.allSettled(apiCalls);
      results.forEach((result, index) => {
        const mode = chatModes[index];
        if (result.status === 'fulfilled') {
          // @ts-ignore
          if (result.value.response.data.status === 'Success') {
            const response = result.value.response.data.data;
            const responseMode: ResponseMode = {
              message: response.message,
              sources: response.info.sources,
              model: response.info.model,
              total_tokens: response.info.total_tokens,
              agent_input_tokens: response.info.agent_input_tokens || 0,
              agent_output_tokens: response.info.agent_output_tokens || 0,
              agent_total_tokens: response.info.agent_total_tokens || 0,
              agent_chunk_details: response.info.agent_chunk_details || [],
              agent_discovered_entities: response.info.agent_discovered_entities || 0,
              agent_discovered_chunks: response.info.agent_discovered_chunks || 0,
              agent_iterations: response.info.agent_iterations || 0,
              response_time: response.info.response_time,
              cypher_query: response.info.cypher_query,
              graphonly_entities: response.info.context ?? [],
              entities: response.info.entities ?? [],
              nodeDetails: response.info.nodedetails,
              error: response.info.error,
              metric_question: response.info?.metric_details?.question ?? '',
              metric_answer: response.info?.metric_details?.answer ?? '',
              metric_contexts: response.info?.metric_details?.contexts ?? '',
            };
            if (index === 0) {
              simulateTypingEffect(chatbotMessageId, responseMode, mode, responseMode.message);
            } else {
              setListMessages((prev) =>
                prev.map((msg) => {
                  return msg.id === chatbotMessageId ? { ...msg, modes: { ...msg.modes, [mode]: responseMode } } : msg;
                })
              );
            }
          } else {
            const response = result.value.response.data;
            const responseMode: ResponseMode = {
              message: response.message,
              error: response.error,
            };
            if (index === 0) {
              simulateTypingEffect(chatbotMessageId, responseMode, response.data, responseMode.message);
            } else {
              setListMessages((prev) =>
                prev.map((msg) => {
                  return msg.id === chatbotMessageId ? { ...msg, modes: { ...msg.modes, [mode]: responseMode } } : msg;
                })
              );
            }
          }
        } else {
          // Log error silently without console
          setListMessages((prev) =>
            prev.map((msg) => {
              return msg.id === chatbotMessageId
                ? {
                    ...msg,
                    modes: {
                      ...msg.modes,
                      [mode]: { message: 'Failed to fetch response for this mode.', error: result.reason },
                    },
                  }
                : msg;
            })
          );
        }
      });
      setListMessages((prev) =>
        prev.map((msg) => (msg.id === chatbotMessageId ? { ...msg, isLoading: false, isTyping: false } : msg))
      );
    } catch (error) {
      // Log error silently without console
      if (error instanceof Error) {
        setListMessages((prev) =>
          prev.map((msg) => {
            return msg.id === chatbotMessageId
              ? {
                  ...msg,
                  isLoading: false,
                  isTyping: false,
                  modes: {
                    [chatModes[0]]: {
                      message: 'An error occurred while processing your request.',
                      error: error.message,
                    },
                  },
                }
              : msg;
          })
        );
      }
    }
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };
  useEffect(() => {
    scrollToBottom();
    setLoading(() => listMessages.some((msg) => msg.isLoading || msg.isTyping));
  }, [listMessages]);

  const handleCopy = (message: string, id: number) => {
    copy(message);
    setListMessages((msgs) =>
      msgs.map((msg) => {
        if (msg.id === id) {
          msg.copying = true;
        }
        return msg;
      })
    );
    setTimeout(() => {
      setListMessages((msgs) =>
        msgs.map((msg) => {
          if (msg.id === id) {
            msg.copying = false;
          }
          return msg;
        })
      );
    }, 2000);
  };

  const handleCancel = (id: number) => {
    cancel();
    setListMessages((msgs) => msgs.map((msg) => (msg.id === id ? { ...msg, speaking: false } : msg)));
  };

  const handleSpeak = (chatMessage: string, id: number) => {
    speak({ text: chatMessage }, typeof window !== 'undefined' && window.speechSynthesis != undefined);
    setListMessages((msgs) => {
      const messageWithSpeaking = msgs.find((msg) => msg.speaking);
      return msgs.map((msg) => (msg.id === id && !messageWithSpeaking ? { ...msg, speaking: true } : msg));
    });
  };

  const handleSwitchMode = (messageId: number, newMode: string) => {
    const activespeechId = listMessages.find((msg) => msg.speaking)?.id;
    if (speaking && messageId === activespeechId) {
      cancel();
      setListMessages((prev) =>
        prev.map((msg) => (msg.id === messageId ? { ...msg, currentMode: newMode, speaking: false } : msg))
      );
    } else {
      setListMessages((prev) => prev.map((msg) => (msg.id === messageId ? { ...msg, currentMode: newMode } : msg)));
    }
  };

  const detailsHandler = useCallback((chat: Messages, previousActiveChat: Messages | null) => {
    const currentMode = chat.modes[chat.currentMode];
    setModelModal(currentMode.model ?? '');
    setSourcesModal(currentMode.sources ?? []);
    setResponseTime(currentMode.response_time ?? 0);
    setTokensUsed(currentMode.total_tokens ?? 0);
    setAgentInputTokens(currentMode.agent_input_tokens ?? 0);
    setAgentOutputTokens(currentMode.agent_output_tokens ?? 0);
    setAgentTotalTokens(currentMode.agent_total_tokens ?? 0);
    setAgentChunkDetails(currentMode.agent_chunk_details ?? []);
    setAgentEntityDetails(currentMode.agent_entity_details ?? []);
    setAgentDiscoveredEntities(currentMode.agent_discovered_entities ?? 0);
    setAgentDiscoveredChunks(currentMode.agent_discovered_chunks ?? 0);
    setAgentIterations(currentMode.agent_iterations ?? 0);
    setcypherQuery(currentMode.cypher_query ?? '');
    setShowInfoModal(true);
    setChatsMode(chat.currentMode ?? '');
    setgraphEntitites(currentMode.graphonly_entities ?? []);
    setEntitiesModal(currentMode.entities ?? []);
    setmessageError(currentMode.error ?? '');
    setNodeDetailsModal(currentMode.nodeDetails ?? {});
    setMetricQuestion(currentMode.metric_question ?? '');
    setMetricContext(currentMode.metric_contexts ?? '');
    setMetricAnswer(currentMode.metric_answer ?? '');
    setActiveChat(chat);
    if (
      (previousActiveChat != null && chat.id != previousActiveChat?.id) ||
      (previousActiveChat != null && chat.currentMode != previousActiveChat.currentMode)
    ) {
      setNodes([]);
      setChunks([]);
      setInfoEntities([]);
      setMetricDetails(null);
    }
    if (previousActiveChat != null && chat.id != previousActiveChat?.id) {
      setMultiModelMetrics([]);
    }
  }, []);

  const speechHandler = useCallback((chat: Messages) => {
    if (chat.speaking) {
      handleCancel(chat.id);
    } else {
      handleSpeak(chat.modes[chat.currentMode]?.message, chat.id);
    }
  }, []);

  return (
    <div className='n-bg-palette-neutral-bg-weak flex! flex-col justify-between min-h-full max-h-full overflow-hidden relative'>
      {isDeleteChatLoading && (
        <div className='chatbot-deleteLoader'>
          <Loader title='Deleting...'></Loader>
        </div>
      )}
      <div
        className={`flex! overflow-y-auto pb-12 min-w-full pl-5 pr-5 chatBotContainer ${
          isChatOnly ? 'min-h-[calc(100dvh-114px)] max-h-[calc(100dvh-114px)]' : ''
        } `}
      >
        <Widget className='n-bg-palette-neutral-bg-weak w-full' header='' isElevated={false}>
          <div className='flex! flex-col gap-4 gap-y-4'>
            {listMessages.map((chat, index) => {
              const messagechatModes = Object.keys(chat.modes);
              return (
                <div
                  ref={messagesEndRef}
                  key={chat.id}
                  className={clsx(`flex! gap-2.5`, {
                    'flex-row': chat.user === 'chatbot',
                    'flex-row-reverse': chat.user !== 'chatbot',
                  })}
                >
                  <div className='w-8 h-8'>
                    {chat.user === 'chatbot' ? (
                      <Avatar
                        className='-ml-4'
                        hasStatus
                        name='KM'
                        size='large'
                        source={ChatBotAvatar}
                        status={connectionStatus ? 'online' : 'offline'}
                        shape='square'
                        type='image'
                      />
                    ) : (
                      <Avatar
                        className=''
                        hasStatus
                        name='KM'
                        size='large'
                        status={connectionStatus ? 'online' : 'offline'}
                        shape='square'
                        type='image'
                      />
                    )}
                  </div>
                  <Widget
                    header=''
                    isElevated={true}
                    className={`p-3! self-start ${isFullScreen ? 'max-w-[55%]' : ''} ${
                      chat.user === 'chatbot' ? 'n-bg-palette-neutral-bg-strong' : 'n-bg-palette-primary-bg-weak'
                    }`}
                  >
                                    <div>
                                      {/* Thinking Steps - sadece son mesaj yüklenirken ve mesaj boşken göster */}
                                      {chat.isLoading && index === listMessages.length - 1 && chat.user === 'chatbot' && thinkingSteps.length > 0 && !chat.modes[chat.currentMode]?.message && (
                                        <div className="thinking-container">
                                          {/* Son thinking step'i göster */}
                                          <div className="flex items-center gap-2 text-sm py-2">
                                            <span className="thinking-dots">
                                              <span className="dot">.</span>
                                              <span className="dot">.</span>
                                              <span className="dot">.</span>
                                            </span>
                                            <span className="opacity-80">{thinkingSteps[thinkingSteps.length - 1]}</span>
                                          </div>
                                        </div>
                                      )}
                                      {/* Loader - mesaj boşken ve thinking yokken */}
                                      {chat.isLoading && index === listMessages.length - 1 && chat.user === 'chatbot' && thinkingSteps.length === 0 && !chat.modes[chat.currentMode]?.message && (
                                        <div className="loader"></div>
                                      )}
                                      {/* Mesaj içeriği */}
                                      <div
                                        className={
                                          !isFullScreen
                                            ? 'max-w-[250px] prose prose-sm sm:prose lg:prose-lg xl:prose-xl'
                                            : 'prose prose-sm sm:prose lg:prose-lg xl:prose-xl max-w-none'
                                        }
                                      >
                                        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw] as any}>
                                          {chat.modes[chat.currentMode]?.message || ''}
                                        </ReactMarkdown>
                                      </div>
                                    </div>
                    <div>
                      <div>
                        <Typography variant='body-small' className='pt-2 font-bold'>
                          {chat.datetime}
                        </Typography>
                      </div>
                      {chat.user === 'chatbot' &&
                        chat.id !== 2 &&
                        !chat.isLoading &&
                        !chat.isTyping &&
                        (!isFullScreen ? (
                          <Flex
                            flexDirection='row'
                            justifyContent={messagechatModes.length > 1 ? 'space-between' : 'unset'}
                            alignItems='center'
                          >
                            <CommonActions
                              chat={chat}
                              copyHandler={handleCopy}
                              detailsHandler={detailsHandler}
                              listMessages={listMessages}
                              speechHandler={speechHandler}
                              activeChat={activeChat}
                            ></CommonActions>
                            {messagechatModes.length > 1 && (
                              <ChatModesSwitch
                                currentMode={chat.currentMode}
                                switchToOtherMode={(index: number) => {
                                  const modes = Object.keys(chat.modes);
                                  const modeswtich = modes[index];
                                  handleSwitchMode(chat.id, modeswtich);
                                }}
                                isFullScreen={false}
                                currentModeIndex={messagechatModes.indexOf(chat.currentMode)}
                                modescount={messagechatModes.length}
                              />
                            )}
                          </Flex>
                        ) : (
                          <Flex flexDirection='row' justifyContent='space-between' alignItems='center'>
                            <Flex flexDirection='row' justifyContent='space-between' alignItems='center'>
                              <CommonActions
                                chat={chat}
                                copyHandler={handleCopy}
                                detailsHandler={detailsHandler}
                                listMessages={listMessages}
                                speechHandler={speechHandler}
                                activeChat={activeChat}
                              ></CommonActions>
                            </Flex>
                            <Box>
                              {messagechatModes.length > 1 && (
                                <ChatModesSwitch
                                  currentMode={chat.currentMode}
                                  switchToOtherMode={(index: number) => {
                                    const modes = Object.keys(chat.modes);
                                    const modeswtich = modes[index];
                                    handleSwitchMode(chat.id, modeswtich);
                                  }}
                                  isFullScreen={isFullScreen}
                                  currentModeIndex={messagechatModes.indexOf(chat.currentMode)}
                                  modescount={messagechatModes.length}
                                />
                              )}
                            </Box>
                          </Flex>
                        ))}
                    </div>
                  </Widget>
                </div>
              );
            })}
          </div>
        </Widget>
      </div>
      <div className='n-bg-palette-neutral-bg-weak flex! flex-col gap-2 bottom-0 p-2.5 w-full'>
        {/* Streaming Toggle */}
        <div className='flex! justify-center items-center gap-2'>
          <Typography variant='body-small'>Normal Chat</Typography>
          <label className='switch'>
            <input
              type='checkbox'
              checked={isStreamingEnabled}
              onChange={(e) => setIsStreamingEnabled(e.target.checked)}
            />
            <span className='slider round'></span>
          </label>
          <Typography variant='body-small'>Streaming Chat</Typography>
        </div>

        <form onSubmit={handleSubmit} className={`flex! gap-2.5 w-full ${!isFullScreen ? 'justify-between' : ''}`}>
          <TextInput
            className={`n-bg-palette-neutral-bg-default flex-grow-7 ${
              isFullScreen ? 'w-[calc(100%-105px)]' : 'w-[70%]'
            }`}
            value={inputMessage}
            isFluid
            onChange={handleInputChange}
            htmlAttributes={{
              type: 'text',
              'aria-label': 'chatbot-input',
              name: 'chatbot-input',
            }}
          />
          <SpotlightTarget id='chatbtn' hasPulse={true} indicatorVariant='border'>
            <ButtonWithToolTip
              label='Q&A Button'
              placement='top'
              text={`Ask a question.`}
              type='submit'
              disabled={loading || !connectionStatus}
              size='medium'
            >
              {buttonCaptions.ask}{' '}
              {selectedFileNames != undefined && selectedFileNames.length > 0 && `(${selectedFileNames.length})`}
            </ButtonWithToolTip>
          </SpotlightTarget>
        </form>
      </div>
      <Suspense fallback={<FallBackDialog />}>
        <Modal
          modalProps={{
            id: 'retrieval-information',
            className: 'n-p-token-4 n-bg-palette-neutral-bg-weak n-rounded-lg',
          }}
          onClose={() => setShowInfoModal(false)}
          isOpen={showInfoModal}
          size={'large'}
        >
          <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
            <IconButton
              size='large'
              htmlAttributes={{
                title: 'download chat info',
              }}
              isClean
              ariaLabel='download chat info'
              isDisabled={metricsLoading || infoLoading}
              onClick={() => {
                downloadClickHandler(
                  {
                    chatResponse: activeChat,
                    chunks,
                    metricDetails,
                    communities,
                    responseTime,
                    entities: infoEntities,
                    nodes,
                    tokensUsed,
                    model,
                    multiModelMetrics,
                  },
                  downloadLinkRef,
                  'graph-builder-chat-details.json'
                );
              }}
            >
              <ArrowDownTrayIconOutline className='n-size-token-7' />
              <TextLink ref={downloadLinkRef} className='hidden!'>
                ""
              </TextLink>
            </IconButton>
            <IconButton
              size='large'
              htmlAttributes={{
                title: 'close pop up',
              }}
              ariaLabel='close pop up'
              isClean
              onClick={() => setShowInfoModal(false)}
            >
              <XMarkIconOutline className='n-size-token-7' />
            </IconButton>
          </div>
          <InfoModal
            sources={sourcesModal}
            model={modelModal}
            entities_ids={entitiesModal}
            response_time={responseTime}
            total_tokens={tokensUsed}
            agent_input_tokens={agentInputTokens}
            agent_output_tokens={agentOutputTokens}
            agent_total_tokens={agentTotalTokens}
            agent_chunk_details={agentChunkDetails}
            agent_entity_details={agentEntityDetails}
            agent_discovered_entities={agentDiscoveredEntities}
            agent_discovered_chunks={agentDiscoveredChunks}
            agent_iterations={agentIterations}
            mode={chatsMode}
            cypher_query={cypherQuery}
            graphonly_entities={graphEntitites}
            error={messageError}
            nodeDetails={nodeDetailsModal}
            metricanswer={metricAnswer}
            metriccontexts={metricContext}
            metricquestion={metricQuestion}
            metricmodel={model}
            nodes={nodes}
            infoEntities={infoEntities}
            relationships={relationships}
            chunks={chunks}
            metricDetails={activeChat != undefined && metricDetails != null ? metricDetails : undefined}
            metricError={activeChat != undefined && metricDetails != null ? (metricDetails.error as string) : ''}
            communities={communities}
            infoLoading={infoLoading}
            metricsLoading={metricsLoading}
            saveInfoEntitites={saveInfoEntitites}
            saveChatRelationships={saveChatRelationships}
            saveChunks={saveChunks}
            saveCommunities={saveCommunities}
            saveMetrics={saveMetrics}
            saveNodes={saveNodes}
            toggleInfoLoading={toggleInfoLoading}
            toggleMetricsLoading={toggleMetricsLoading}
            saveMultimodemetrics={saveMultimodemetrics}
            activeChatmodes={activeChat?.modes}
            multiModelMetrics={multiModelMetrics}
          />
        </Modal>
      </Suspense>
    </div>
  );
};

export default Chatbot;
