import React, { useState, useRef } from 'react';
import { Button, Dialog, TextArea, Select, Flex, Typography } from '@neo4j-ndl/react';
import { extractQABased, convertToMarkdown } from '../../API/Index';
import { UserCredentials } from '../../types';

interface QABasedExtractionModalProps {
  open: boolean;
  onClose: () => void;
  userCredentials: UserCredentials;
  onSuccess: (data: any) => void;
}

const QABasedExtractionModal: React.FC<QABasedExtractionModalProps> = ({
  open,
  onClose,
  userCredentials,
  onSuccess,
}) => {
  const [loading, setLoading] = useState(false);
  const [processingFile, setProcessingFile] = useState(false);
  const [fileName, setFileName] = useState('');
  const [documentText, setDocumentText] = useState('');
  const [model, setModel] = useState('openai_gpt_4.1');
  const [domain, setDomain] = useState('insurance'); // Default domain set to 'insurance'
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [useChunking, setUseChunking] = useState(false);
  const [fileProcessMessage, setFileProcessMessage] = useState('');
  const [messageType, setMessageType] = useState<'success' | 'error' | 'info'>('info');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleClose = () => {
    // Modal kapanırken state'i temizle
    setFileName('');
    setDocumentText('');
    setDomain('');
    setSelectedFile(null);
    setLoading(false);
    setProcessingFile(false);
    setUseChunking(false);
    setFileProcessMessage('');
    setMessageType('info');
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
    onClose();
  };

  const handleFileSelect = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    setSelectedFile(file);
    setFileName(file.name);
    setProcessingFile(true);

    try {
      // Docling ile PDF'i markdown'a çevir
      const response = await convertToMarkdown(file);

      if (response.data.status === 'success') {
        setDocumentText(response.data.markdown);

        // Cache bilgisini kullanıcıya göster
        if (response.data.from_cache) {
          // Cache'den alındıysa kısa bilgi ver
          setFileProcessMessage(`✅ Dosya cache'den hızlıca yüklendi! (${response.data.markdown_size} karakter)`);
          setMessageType('success');
        } else {
          // İlk kez işlendiyse ve cache'e kaydedildiyse bilgi ver
          setFileProcessMessage(
            `📝 Dosya başarıyla markdown'a çevrildi ve cache'e kaydedildi! Orijinal boyut: ${response.data.original_size} byte, Markdown boyutu: ${response.data.markdown_size} karakter`
          );
          setMessageType('success');
        }
      } else {
        throw new Error('Dosya işleme hatası');
      }
    } catch (error) {
      setFileProcessMessage('❌ Dosya işlenirken bir hata oluştu. Lütfen metni manuel olarak yapıştırın.');
      setMessageType('error');

      // Hata durumunda file input'u temizle
      setSelectedFile(null);
      setFileName('');
      setDocumentText('');
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    } finally {
      setProcessingFile(false);
    }
  };

  const handleExtraction = async () => {
    if (!fileName || !documentText) {
      return;
    }

    setLoading(true);
    try {
      // Chunk ayırma opsiyonel - eğer seçilmişse [PAGE BREAK] ile böl
      let chunks: string[];

      if (useChunking && documentText.includes('[PAGE BREAK]')) {
        // [PAGE BREAK] ayracı ile böl ve boş olmayan parçaları al
        chunks = documentText
          .split('[PAGE BREAK]')
          .map((chunk: string) => chunk.trim())
          .filter((chunk: string) => chunk.length > 0);
      } else {
        // Chunking kullanılmıyorsa veya ayraç yoksa tüm metni tek parça olarak gönder
        chunks = [documentText];
      }

      const response = await extractQABased({
        document_chunks: chunks,
        file_name: fileName,
        model: model,
        domain: domain || undefined,
        uri: userCredentials.uri,
        userName: userCredentials.userName,
        password: userCredentials.password,
        database: userCredentials.database,
        email: userCredentials.email,
      });

      if (response.data.status === 'Success') {
        onSuccess(response.data.data);
        handleClose();
      } else {
        // Handle error
      }
    } catch (error) {
      // Handle error
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog isOpen={open} onClose={handleClose} aria-labelledby='qa-extraction-dialog-title' size='large'>
      <Dialog.Header>
        <h2 id='qa-extraction-dialog-title'>🤖 QA Tabanlı Şema Çıkarma</h2>
      </Dialog.Header>
      <Dialog.Content>
        <Flex flexDirection='column' gap='4'>
          {/* Dosya Seçme Bölümü */}
          <div>
            <Typography variant='body-medium' style={{ marginBottom: '8px', fontWeight: 'bold' }}>
              Dosya Seçimi
            </Typography>
            <input
              type='file'
              accept='.pdf,.doc,.docx,.txt'
              onChange={handleFileChange}
              ref={fileInputRef}
              style={{ display: 'none' }}
            />
            <Flex gap='2' alignItems='center'>
              <Button onClick={handleFileSelect} isDisabled={processingFile} size='medium'>
                {processingFile ? '⏳ Dosya İşleniyor...' : '📁 Dosya Seç'}
              </Button>
              {selectedFile && (
                <Typography variant='body-small' className='text-green-600'>
                  ✅ {selectedFile.name}
                </Typography>
              )}
            </Flex>
            <Typography variant='body-small' className='text-gray-600' style={{ marginTop: '4px' }}>
              PDF, DOC, DOCX veya TXT dosyası seçin. Docling ile markdown'a çevrilecek.
            </Typography>

            {/* Dosya işleme mesajı */}
            {fileProcessMessage && (
              <div
                className={`mt-3 p-3 rounded-md border ${
                  messageType === 'success'
                    ? 'bg-green-50 border-green-200 text-green-800'
                    : messageType === 'error'
                      ? 'bg-red-50 border-red-200 text-red-800'
                      : 'bg-blue-50 border-blue-200 text-blue-800'
                }`}
              >
                <Typography variant='body-small'>{fileProcessMessage}</Typography>
              </div>
            )}
          </div>

          <div>
            <TextArea
              label='Dosya Adı (Otomatik Doldurulur)'
              value={fileName}
              htmlAttributes={{
                onChange: (e: any) => setFileName(e.target.value),
              }}
              placeholder='Seçilen dosyanın adı otomatik doldurulacak'
              isFluid={true}
              size='medium'
              isReadOnly={Boolean(selectedFile)}
            />
          </div>

          <div>
            <Select
              type='select'
              label='LLM Model'
              selectProps={{
                value: { label: model, value: model },
                onChange: (option: any) => setModel(option?.value || 'openai_gpt_4.1'),
                options: [
                  { label: 'GPT-4.1', value: 'openai_gpt_4.1' },
                  { label: 'GPT-4o', value: 'openai_gpt_4o' },
                  { label: 'GPT-o3-mini', value: 'openai_gpt_o3_mini' },
                  { label: 'GPT-4o Mini', value: 'openai_gpt_4o_mini' },
                  { label: 'Gemini 1.5 Pro', value: 'gemini_1.5_pro' },
                ],
              }}
              isFluid={true}
              size='medium'
            />
          </div>

          <div>
            <Select
              type='select'
              label='Domain (Opsiyonel)'
              selectProps={{
                value: domain ? { label: domain, value: domain } : null,
                onChange: (option: any) => setDomain(option?.value || 'insurance'),
                options: [
                  { label: 'Otomatik Tespit', value: '' },
                  { label: 'Sigorta', value: 'insurance' },
                  { label: 'Hukuki', value: 'legal' },
                  { label: 'Finansal', value: 'financial' },
                ],
                isClearable: true,
              }}
              isFluid={true}
              size='medium'
            />
          </div>

          {/* Chunking Seçeneği */}
          <div>
            <Typography variant='body-medium' style={{ marginBottom: '8px', fontWeight: 'bold' }}>
              Metin İşleme Seçeneği
            </Typography>
            <Flex gap='2' alignItems='center'>
              <input
                type='checkbox'
                id='useChunking'
                checked={useChunking}
                onChange={(e) => setUseChunking(e.target.checked)}
              />
              <label htmlFor='useChunking'>
                <Typography variant='body-small'>Metni parçalara böl ([PAGE BREAK] ayracı ile)</Typography>
              </label>
            </Flex>
            <Typography variant='body-small' className='text-gray-600' style={{ marginTop: '4px' }}>
              {useChunking
                ? 'Metin [PAGE BREAK] ayracına göre parçalara bölünecek. Ayraç yoksa tüm metin tek parça olarak işlenecek.'
                : 'Tüm metin tek parça olarak işlenecek (önerilen).'}
            </Typography>
          </div>

          <div>
            <TextArea
              label={selectedFile ? 'Belge Metni (Dosyadan Otomatik Çıkarıldı)' : 'Belge Metni (Manuel Giriş)'}
              value={documentText}
              htmlAttributes={{
                onChange: (e: any) => setDocumentText(e.target.value),
                rows: 8,
              }}
              placeholder={
                selectedFile
                  ? 'Seçilen dosya Docling ile işlenecek ve buraya otomatik eklenecek...'
                  : 'Belge metnini buraya yapıştırın veya yukarıdan dosya seçin...'
              }
              isFluid={true}
              size='medium'
              style={{ resize: 'vertical' }}
              helpText={
                processingFile
                  ? 'Dosya işleniyor, lütfen bekleyin...'
                  : selectedFile
                    ? 'Dosyadan çıkarılan metin. Gerekirse düzenleyebilirsiniz.'
                    : 'Manuel metin girişi veya yukarıdan dosya seçerek otomatik çıkarım yapabilirsiniz.'
              }
            />
          </div>
        </Flex>
      </Dialog.Content>
      <Dialog.Actions>
        <Button onClick={handleClose}>İptal</Button>
        <Button onClick={handleExtraction} isLoading={loading} isDisabled={!fileName || !documentText}>
          Şema Çıkar
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
};

export default QABasedExtractionModal;
