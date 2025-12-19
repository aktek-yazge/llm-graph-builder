// Chat Stream API servisi - Server-Sent Events (SSE) kullanarak gerçek zamanlı chat
import { url } from '../utils/Utils';

export interface ChatStreamMessage {
  type: 'status' | 'message_chunk' | 'complete' | 'error' | 'thinking_step';
  message?: string;
  content?: string;
  full_message?: string;
  status?: string;
  data?: any;
  error?: string;
  is_complete?: boolean;
  word_index?: number;
  total_words?: number;
  elapsed_time?: string;
  timestamp?: string;
  session_id?: string;
  user?: string;
  details?: string;  // thinking_step detayları
  result_type?: string;  // thinking_step sonuç tipi (success, warning, error)
  tool_name?: string;  // thinking_step tool adı (debug için)
  info?: {
    sources?: any[];
    model?: string;
    nodedetails?: any;
    total_tokens?: number;
    agent_input_tokens?: number;
    agent_output_tokens?: number;
    agent_total_tokens?: number;
    agent_chunk_details?: Array<{
      document: string;
      page: number;
      relevance: number;
      preview: string;
    }>;
    agent_entity_details?: Array<{
      id: string;
      type: string;
      labels: string[];
    }>;
    agent_discovered_entities?: number;
    agent_discovered_chunks?: number;
    agent_iterations?: number;
    response_time?: number;
    mode?: string;
    entities?: any;
    cypher_query?: string;
    context?: any[];
    error?: string;
    personPolicyInfo?: any[]; // YENI: Person Policy Info
    metric_details?: {
      question?: string;
      answer?: string;
      contexts?: string;
    };
  };
}

export interface ChatStreamOptions {
  question: string;
  session_id: string;
  question_id: string;  // Unique ID per question for log correlation
  model: string;
  mode: string;
  document_names?: (string | undefined)[];
  uri?: string;
  userName?: string;
  password?: string;
  database?: string;
  email?: string;
}

export class ChatStreamAPI {
  private abortController: AbortController | null = null;

  /**
   * Streaming chat başlat - Fetch API ile POST stream
   */
  public async startChatStream(
    options: ChatStreamOptions,
    onMessage: (message: ChatStreamMessage) => void,
    onError?: (error: Error) => void,
    onComplete?: () => void
  ): Promise<void> {
    // Önceki request varsa iptal et
    this.stopChatStream();

    // AbortController oluştur
    this.abortController = new AbortController();

    try {
      // FormData oluştur (normal chat_bot endpoint'i gibi)
      const formData = new FormData();
      formData.append('question', options.question);
      formData.append('session_id', options.session_id);
      formData.append('question_id', options.question_id);  // Log correlation
      formData.append('model', options.model);
      formData.append('mode', options.mode);

      if (options.document_names) {
        formData.append('document_names', JSON.stringify(options.document_names));
      }
      if (options.uri) {
        formData.append('uri', options.uri);
      }
      if (options.userName) {
        formData.append('userName', options.userName);
      }
      if (options.password) {
        formData.append('password', options.password);
      }
      if (options.database) {
        formData.append('database', options.database);
      }
      if (options.email) {
        formData.append('email', options.email);
      }

      const streamUrl = `${url()}/chat_bot_stream`;

      // Fetch ile POST request gönder
      const response = await fetch(streamUrl, {
        method: 'POST',
        body: formData,
        signal: this.abortController.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('Response body reader not available');
      }

      const decoder = new TextDecoder();

      // Stream okuma işlemi
      const processStream = async () => {
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            onComplete?.();
            break;
          }

          const chunk = decoder.decode(value, { stream: true });
          this.processChunk(chunk, onMessage);
        }
      };

      await processStream();
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        onError?.(error);
      }
    }
  }

  /**
   * Chunk'ları işle ve mesajları parse et
   */
  private processChunk(chunk: string, onMessage: (message: ChatStreamMessage) => void): void {
    // Debug için chunk'ı logla
    // eslint-disable-next-line no-console
    console.log('Received chunk:', chunk);

    const lines = chunk.split('\n');

    for (const line of lines) {
      const trimmedLine = line.trim();
      if (trimmedLine.startsWith('data: ')) {
        this.parseSSEMessage(trimmedLine, onMessage);
      } else if (trimmedLine && !trimmedLine.startsWith('event:') && !trimmedLine.startsWith('id:')) {
        // Eğer data: prefix'i yoksa da JSON parse etmeyi dene
        try {
          const message: ChatStreamMessage = JSON.parse(trimmedLine);
          // eslint-disable-next-line no-console
          console.log('Parsed message without data prefix:', message);
          onMessage(message);
        } catch (e) {
          // eslint-disable-next-line no-console
          console.log('Non-JSON line:', trimmedLine);
        }
      }
    }
  }

  /**
   * SSE mesajını parse et
   */
  private parseSSEMessage(line: string, onMessage: (message: ChatStreamMessage) => void): void {
    try {
      let data = line.slice(6); // 'data: ' kısmını çıkar

      // Eğer ikinci bir 'data: ' prefix'i varsa onu da çıkar
      if (data.startsWith('data: ')) {
        data = data.slice(6);
      }

      if (data.trim()) {
        const message: ChatStreamMessage = JSON.parse(data);
        // eslint-disable-next-line no-console
        console.log('Parsed SSE message:', message);
        onMessage(message);
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('Failed to parse SSE message:', line, e);
    }
  }

  /**
   * Streaming chat durdur
   */
  public stopChatStream(): void {
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
  }

  /**
   * Bağlantı durumu kontrolü
   */
  public isConnected(): boolean {
    return this.abortController !== null;
  }

  /**
   * Bağlantı durumu string olarak
   */
  public getConnectionState(): string {
    if (this.abortController) {
      return 'connected';
    }
    return 'disconnected';
  }
}

// Singleton instance
export const chatStreamAPI = new ChatStreamAPI();
