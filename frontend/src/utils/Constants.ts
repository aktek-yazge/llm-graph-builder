import { NvlOptions } from '@neo4j-nvl/base';
import { GraphType, OptionType, PatternOption } from '../types';
import { getDateTime, getDescriptionForChatMode } from './Utils';
import chatbotmessages from '../assets/ChatbotMessages.json';
import schemaExamples from '../assets/newSchema.json';
export const APP_SOURCES =
  process.env.VITE_REACT_APP_SOURCES !== ''
    ? (process.env.VITE_REACT_APP_SOURCES?.split(',') as string[])
    : ['s3', 'local', 'wiki', 'youtube', 'web'];

export const llms =
  process.env?.VITE_LLM_MODELS?.trim() != ''
    ? (process.env.VITE_LLM_MODELS?.split(',') as string[])
    : [
        'openai_gpt_4o',
        'openai_gpt_4o_mini',
        'openai_gpt_4.1',
        'openai_gpt_4.1_mini',
        'openai_gpt_o3_mini',
        'gemini_1.5_pro',
        'gemini_1.5_flash',
        'gemini_2.0_flash',
        'gemini_2.5_pro',
        'diffbot',
        'azure_ai_gpt_35',
        'azure_ai_gpt_4o',
        'ollama_llama3',
        'groq_llama3_70b',
        'anthropic_claude_4_sonnet',
        'fireworks_llama4_maverick',
        'fireworks_llama4_scout',
        'fireworks_qwen72b_instruct',
        'bedrock_nova_micro_v1',
        'bedrock_nova_lite_v1',
        'bedrock_nova_pro_v1',
        'fireworks_deepseek_r1',
        'fireworks_deepseek_v3',
        'llama4_maverick',
        'fireworks_qwen3_30b',
        'fireworks_qwen3_235b',
      ];

export const supportedLLmsForRagas = [
  'openai_gpt_4',
  'openai_gpt_4o',
  'openai_gpt_4o_mini',
  'openai_gpt_4.1',
  'openai_gpt_4.1_mini',
  'gemini_1.5_pro',
  'gemini_1.5_flash',
  'gemini_2.0_flash',
  'gemini_2.5_pro',
  'azure_ai_gpt_35',
  'azure_ai_gpt_4o',
  'groq_llama3_70b',
  'anthropic_claude_4_sonnet',
  'fireworks_llama4_maverick',
  'fireworks_llama4_scout',
  'openai_gpt_o3_mini',
  'llama4_maverick',
  'fireworks_qwen3_30b',
  'fireworks_qwen3_235b',
];
export const supportedLLmsForGroundTruthMetrics = [
  'openai_gpt_4',
  'openai_gpt_4o',
  'openai_gpt_4o_mini',
  'openai_gpt_4.1',
  'openai_gpt_4.1_mini',
  'azure_ai_gpt_35',
  'azure_ai_gpt_4o',
  'groq_llama3_70b',
  'anthropic_claude_4_sonnet',
  'fireworks_llama4_maverick',
  'fireworks_llama4_scout',
  'openai_gpt_o3_mini',
  'llama4_maverick',
  'fireworks_qwen3_30b',
  'fireworks_qwen3_235b',
];
export const prodllms =
  process.env.VITE_LLM_MODELS_PROD?.trim() != ''
    ? (process.env.VITE_LLM_MODELS_PROD?.split(',') as string[])
    : ['openai_gpt_4o', 'openai_gpt_4o_mini', 'diffbot', 'gemini_2.0_flash'];

export const chatModeLables = {
  vector: 'vector',
  graph: 'graph',
  'graph+vector': 'graph_vector',
  fulltext: 'fulltext',
  'graph+vector+fulltext': 'graph_vector_fulltext',
  'entity search+vector': 'entity_vector',
  unavailableChatMode: 'Dosyalar seçildiğinde sohbet modu kullanılamaz',
  selected: 'Seçili',
  'global search+vector+fulltext': 'global_vector',
};
export const chatModeReadableLables: Record<string, string> = {
  vector: 'vector',
  graph: 'graph',
  graph_vector: 'graph+vector',
  fulltext: 'fulltext',
  graph_vector_fulltext: 'graph+vector+fulltext',
  entity_vector: 'entity search+vector',
  unavailableChatMode: 'Dosyalar seçildiğinde sohbet modu kullanılamaz',
  selected: 'Seçili',
  global_vector: 'global search+vector+fulltext',
};
export const chatModes =
  process.env?.VITE_CHAT_MODES?.trim() != ''
    ? process.env.VITE_CHAT_MODES?.split(',').map((mode) => ({
        mode: mode.trim(),
        description: getDescriptionForChatMode(mode.trim()),
      }))
    : [
        {
          mode: chatModeLables.vector,
          description: 'Vektör indekslemesi kullanarak metin parçaları üzerinde anlamsal benzerlik araması yapar.',
        },
        {
          mode: chatModeLables.graph,
          description: 'Graph veritabanından kesin veri alımı için metni Cypher sorgularına çevirir.',
        },
        {
          mode: chatModeLables['graph+vector'],
          description:
            'Bağlamsal olarak geliştirilmiş anlamsal arama için vektör indeksleme ve graph bağlantılarını birleştirir.',
        },
        {
          mode: chatModeLables.fulltext,
          description:
            'Metin parçaları üzerinde tam metin indeksleme kullanarak hızlı, anahtar kelime tabanlı arama yapar.',
        },
        {
          mode: chatModeLables['graph+vector+fulltext'],
          description: 'Kapsamlı arama sonuçları için vektör, graph ve tam metin indekslemesini entegre eder.',
        },
        {
          mode: chatModeLables['entity search+vector'],
          description: 'Yüksek ilgili varlık tabanlı arama için entity nodeları üzerinde vektör indeksleme kullanır.',
        },
        {
          mode: chatModeLables['global search+vector+fulltext'],
          description:
            'Global olarak doğru, bağlam farkında cevaplar sağlamak için community nodeları vektör ve tam metin indeksleme kullanır.',
        },
      ];
export const chunkSize = process.env.VITE_CHUNK_SIZE ? Number(process.env.VITE_CHUNK_SIZE) : 1 * 1024 * 1024;
export const tokenchunkSize = process.env.VITE_TOKENS_PER_CHUNK ? Number(process.env.VITE_TOKENS_PER_CHUNK) : 100;
export const chunkOverlap = process.env.VITE_CHUNK_OVERLAP ? Number(process.env.VITE_CHUNK_OVERLAP) : 20;
export const chunksToCombine = process.env.VITE_CHUNK_TO_COMBINE ? Number(process.env.VITE_CHUNK_TO_COMBINE) : 1;
export const defaultTokenChunkSizeOptions = [50, 100, 200, 400, 1000];
export const defaultChunkOverlapOptions = [10, 20, 30, 40, 50];
export const defaultChunksToCombineOptions = [1, 2, 3, 4, 5, 6];
export const timeperpage = process.env.VITE_TIME_PER_PAGE ? Number(process.env.VITE_TIME_PER_PAGE) : 50;
export const timePerByte = 0.2;
export const largeFileSize = process.env.VITE_LARGE_FILE_SIZE
  ? Number(process.env.VITE_LARGE_FILE_SIZE)
  : 5 * 1024 * 1024;

export const tooltips = {
  generateGraph: 'Seçili dosyalardan graph oluştur',
  deleteFile: 'Silmek için bir veya daha fazla dosya seç',
  showGraph: "Oluşturulan graph'i önizle.",
  bloomGraph: "Graph'i Bloom'da görselleştir",
  deleteSelectedFiles: 'Silinecek dosya/dosyalar',
  documentation: 'Dokümantasyon',
  github: 'GitHub Sorunları',
  theme: 'Açık / Koyu mod',
  settings: 'Entity Graph Çıkarma Ayarları',
  chat: 'Sohbet başlat',
  sources: 'Dosya yükle',
  deleteChat: 'Sil',
  maximise: 'Büyüt',
  copy: 'Panoya Kopyala',
  copied: 'Kopyalandı',
  stopSpeaking: 'Konuşmayı Durdur',
  textTospeech: 'Metni Sese Dönüştür',
  createSchema: 'Metinden şema tanımla',
  useExistingSchema: 'Veritabanından şema getir',
  clearChat: 'Sohbet Geçmişini Temizle',
  continue: 'Devam Et',
  clearGraphSettings: 'Yapılandırılan Graph Şemasını Temizle',
  applySettings: 'Graph Şemasını Uygula',
  openChatPopout: 'Sohbet',
  downloadChat: 'Konuşmayı İndir',
  visualizeGraph: 'Graph Şemasını Görselleştir',
  additionalInstructions: 'Şema için talimatları analiz et',
  predinedSchema: 'Önceden Tanımlanmış Şema',
  dataImporterJson: 'Data Importer JSON',
};
export const PRODMODLES = ['openai_gpt_4o', 'openai_gpt_4o_mini', 'diffbot', 'gemini_1.5_flash'];
export const buttonCaptions = {
  exploreGraphWithBloom: "Graph'i Keşfet",
  showPreviewGraph: 'Graph Önizleme',
  deleteFiles: 'Dosyaları Sil',
  generateGraph: 'Graph Oluştur',
  dropzoneSpan: 'Belgeler, Görseller, Yapılandırılmamış metin',
  youtube: 'Youtube',
  gcs: 'GCS',
  amazon: 'Amazon S3',
  noLables: 'Veritabanında Etiket Bulunamadı',
  dropYourCreds: 'Neo4j kimlik bilgileri dosyanızı buraya bırakın',
  analyze: 'Graph şeması çıkarmak için metni analiz et',
  connect: 'Bağlan',
  disconnect: 'Bağlantıyı Kes',
  submit: 'Gönder',
  connectToNeo4j: 'Grapha Bağlan',
  cancel: 'İptal',
  details: 'Detaylar',
  continueSettings: 'Devam Et',
  clearSettings: 'Şemayı Temizle',
  ask: 'Sor',
  applyGraphSchema: 'Uygula',
  provideAdditionalInstructions: 'Entity Çıkarımı için Ek Talimatlar Sağlayın',
  analyzeInstructions: 'Talimatları Analiz Et',
  helpInstructions: 'Anahtar konulara odaklanmak gibi varlık çıkarımı için spesifik talimatlar sağlayın.',
  importDropzoneSpan: 'JSON Belgeleri',
};

export const POST_PROCESSING_JOBS: { title: string; description: string }[] = [
  {
    title: 'connect_documents_by_entities',
    description: `Ortak varlıkları paylaşan belgeler arasında anlamsal ilişkiler oluşturur. Bu "aynı kişiye ait tüm poliçeler" 
                veya "aynı sigorta şirketinden belgeler" gibi bağlantıları keşfetmeyi sağlar. 
                Belge analizini geliştirir ve müşteriler için poliçe portföy analizini mümkün kılar.`,
  },
  {
    title: 'materialize_text_chunk_similarities',
    description: `Bu seçenek, bilgi graph'ınızdaki farklı bilgi parçaları (chunk'lar) arasındaki bağlantıları iyileştirir. 
                Benzerlik eşiği (KNN_MIN_SCORE 0.8) ile k-en yakın komşu algoritmasını kullanarak, bu işlem yüksek anlamsal 
                benzerliği olan chunk'ları tanımlar ve bağlar. Bu, daha bağlantılı ve ayrıntılı bir bilgi temsili oluşturur 
                ve daha doğru, ilgili arama sonuçlarını mümkün kılar.`,
  },
  {
    title: 'enable_hybrid_search_and_fulltext_search_in_bloom',
    description: `Bu seçenek, bilgi graph'ınızdaki arama özelliklerini optimize eder. Veritabanı etiketlerinde tam metin 
                indeksini yeniden oluşturur ve daha hızlı, verimli bilgi alımını sağlar. Bu, özellikle büyük bilgi 
                graph'ları için faydalıdır çünkü anahtar kelime tabanlı aramaları önemli ölçüde hızlandırır ve genel 
                sorgu performansını iyileştirir.`,
  },
  {
    title: 'materialize_entity_similarities',
    description: `Anlamsal anlamlarını yakalayan sayısal temsiller (embedding'ler) üreterek varlık analizini geliştirir. 
                Bu, benzer varlıkları kümeleme, kopyaları tanımlama ve benzerlik tabanlı aramalar yapma gibi görevleri 
                kolaylaştırır.`,
  },
  {
    title: 'enable_communities',
    description:
      'GraphRAG yetenekleri hem yerel hem de global arama için entities arası community oluşturmayı etkinleştir.',
  },
  {
    title: 'graph_schema_consolidation',
    description:
      "Bu seçenek, büyük graph şemaları için LLM'yi kullanarak çok sayıda node etiketini ve ilişki türünü daha az, daha ilgili olanlara birleştirir ve bunu çıkarılan ve mevcut graph'e uygular",
  },
  {
    title: 'entity_relationship_post_processing',
    description:
      'Belirli varlık türlerini tanımlanan ilişkilerle hedef nodelara bağlar. DocumentYear, PolicyStartYear gibi zamansal varlıkları Document nodelarına bağlamak için kullanışlıdır.',
  },
];
export const RETRY_OPIONS = [
  'start_from_beginning',
  'delete_entities_and_start_from_beginning',
  'start_from_last_processed_position',
];
export const batchSize: number = Number(process.env.VITE_BATCH_SIZE ?? '2');

// Graph Constants
export const document = `+ [docs]`;

export const chunks = `+ collect { MATCH p=(c)-[:NEXT_CHUNK]-() RETURN p } // chunk-chain
+ collect { MATCH p=(c)-[:SIMILAR]-() RETURN p } // similar-chunks`;

export const entities = `+ collect { OPTIONAL MATCH (c:Chunk)-[:HAS_ENTITY]->(e), p=(e)-[*0..1]-(:!Chunk) RETURN p}`;

export const docEntities = `+ [docs] 
+ collect { MATCH (c:Chunk)-[:HAS_ENTITY]->(e), p=(e)--(:!Chunk) RETURN p }`;

export const docChunks = `+[chunks]
+collect {MATCH p=(c)-[:FIRST_CHUNK]-() RETURN p} //first chunk
+ collect { MATCH p=(c)-[:NEXT_CHUNK]-() RETURN p } // chunk-chain
+ collect { MATCH p=(c)-[:SIMILAR]-() RETURN p } // similar-chunk`;

export const chunksEntities = `+ collect { MATCH p=(c)-[:NEXT_CHUNK]-() RETURN p } // chunk-chain

+ collect { MATCH p=(c)-[:SIMILAR]-() RETURN p } // similar-chunks
//chunks with entities
+ collect { OPTIONAL MATCH p=(c:Chunk)-[:HAS_ENTITY]->(e)-[*0..1]-(:!Chunk) RETURN p }`;

export const docChunkEntities = `+[chunks]
+collect {MATCH p=(c)-[:FIRST_CHUNK]-() RETURN p} //first chunk
+ collect { MATCH p=(c)-[:NEXT_CHUNK]-() RETURN p } // chunk-chain
+ collect { MATCH p=(c)-[:SIMILAR]-() RETURN p } // similar-chunks
//chunks with entities
+ collect { OPTIONAL MATCH p=(c:Chunk)-[:HAS_ENTITY]->(e)-[*0..1]-(:!Chunk) RETURN p }`;

export const nvlOptions: NvlOptions = {
  allowDynamicMinZoom: true,
  disableWebGL: true,
  maxZoom: 3,
  minZoom: 0.05,
  relationshipThreshold: 0.55,
  useWebGL: false,
  instanceId: 'graph-preview',
  initialZoom: 1,
};

export const queryMap: {
  Document: string;
  Chunks: string;
  Entities: string;
  DocEntities: string;
  DocChunks: string;
  ChunksEntities: string;
  DocChunkEntities: string;
} = {
  Document: 'document',
  Chunks: 'chunks',
  Entities: 'entities',
  DocEntities: 'docEntities',
  DocChunks: 'docChunks',
  ChunksEntities: 'chunksEntities',
  DocChunkEntities: 'docChunkEntities',
};

// export const graphQuery: string = queryMap.DocChunkEntities;
export const graphView: OptionType[] = [
  { label: 'Sözcüksel Graph', value: queryMap.DocChunks },
  { label: 'Entity Graph', value: queryMap.Entities },
  { label: "Bilgi Graph'i", value: queryMap.DocChunkEntities },
];

export const intitalGraphType = (isGDSActive: boolean): GraphType[] => {
  return isGDSActive
    ? ['DocumentChunk', 'Entities', 'Communities'] // GDS is active, include communities
    : ['DocumentChunk', 'Entities']; // GDS is inactive, exclude communities
};

export const graphLabels = {
  showGraphView: 'showGraphView',
  chatInfoView: 'chatInfoView',
  generateGraph: 'Oluşturulan Graph',
  inspectGeneratedGraphFrom: "Şundan Oluşturulan Graph'i İncele",
  document: 'Document',
  chunk: 'Chunk',
  documentChunk: 'DocumentChunk',
  entities: 'Entities',
  resultOverview: 'Sonuç Özeti',
  totalNodes: 'Toplam Node',
  noEntities: 'Entity Bulunamadı',
  selectCheckbox: 'Graph görünümü için en az bir onay kutusu seç',
  totalRelationships: 'Toplam İlişki',
  nodeSize: 30,
  docChunk: 'Document & Chunk',
  community: 'Communities',
  noNodesRels: 'Node ve relation yok',
  neighborView: 'neighborView',
  chunksInfo: 'Aynı anda 50 chunk görselleştiriyoruz',
  showSchemaView: 'showSchemaView',
  renderSchemaGraph: 'Veritabanı Şemasından Graph',
  generatedGraphFromUserSchema: 'Kullanıcı Tanımlı Şemadan Oluşturulan Graph',
};

export const RESULT_STEP_SIZE = 25;

export const connectionLabels = {
  notConnected: 'Bağlı Değil',
  graphDataScience: 'Graph Data Science',
  graphDatabase: 'Graph Database',
  greenStroke: 'green',
  redStroke: 'red',
};

export const getDefaultMessage = () => {
  return [{ ...chatbotmessages.listMessages[0], datetime: getDateTime() }];
};

export const appLabels = {
  ownSchema: 'Veya Kendi Şemanızı Tanımlayın',
  predefinedSchema: 'Önceden Tanımlanmış Bir Şema Seçin',
  chunkingConfiguration: 'Bir Chunk Yapılandırması Seçin',
  graphPatternTuple: 'Graph Deseni',
  selectedPatterns: 'Seçili Desenler',
  dataImporterSchema: "Data Importer'dan Şema",
};

export const LLMDropdownLabel = {
  disabledModels: 'Devre dışı modeller geliştirme sürümünde mevcuttur. ',
  devEnv: 'geliştirme ortamımızda',
};
export const getDefaultSchemaExamples = () => {
  return schemaExamples.map((example) => ({
    label: example.schema,
    value: JSON.stringify(example.triplet),
  }));
};

export function mergeNestedObjects(objects: Record<string, Record<string, number>>[]) {
  return objects.reduce((merged, obj) => {
    for (const key in obj) {
      if (!merged[key]) {
        merged[key] = {};
      }
      for (const innerKey in obj[key]) {
        merged[key][innerKey] = obj[key][innerKey];
      }
    }
    return merged;
  }, {});
}
export function getStoredSchema() {
  const storedSchemas = localStorage.getItem('selectedSchemas');
  if (storedSchemas) {
    const parsedSchemas = JSON.parse(storedSchemas);
    return parsedSchemas.selectedOptions;
  }
  return [];
}
export const metricsinfo: Record<string, string> = {
  faithfulness: 'Cevabın sağlanan bilgiyi ne kadar doğru yansıttığını belirler',
  answer_relevancy: 'Cevabın kullanıcının sorusunu ne kadar iyi karşıladığını belirler.',
  rouge_score: 'Oluşturulan cevabın referans cevapla kelime kelime ne kadar eşleştiğini belirler.',
  semantic_score: 'Oluşturulan cevabın referans cevabın anlamını ne kadar iyi anladığını belirler.',
  context_entity_recall: 'Oluşturulan cevap ve alınan bağlamlarda bulunan varlıkların geri çağırma oranını belirler',
};
export const EXPIRATION_DAYS = 3;
// Set to 'false' to enable JWT authentication, 'true' to skip
export const SKIP_AUTH = (process.env.VITE_SKIP_AUTH ?? 'false') == 'true';

export const sourceOptions: PatternOption[] = [{ label: 'Person', value: 'Person' }];
export const typeOptions: PatternOption[] = [{ label: 'WORKS_FOR', value: 'WORKS_FOR' }];
export const targetOptions: PatternOption[] = [{ label: 'Company', value: 'Company' }];

export const LOCAL_KEYS = {
  source: 'customSourceOptions',
  type: 'customTypeOptions',
  target: 'customTargetOptions',
};
