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
  const [domain, setDomain] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [useChunking, setUseChunking] = useState(false);
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
          setTimeout(() => {
            alert(
              `✅ Dosya cache'den hızlıca yüklendi!\n\nDosya: ${file.name}\nMarkdown boyutu: ${response.data.markdown_size} karakter`
            );
          }, 500);
        } else {
          // İlk kez işlendiyse ve cache'e kaydedildiyse bilgi ver
          setTimeout(() => {
            alert(
              `📝 Dosya başarıyla markdown'a çevrildi ve cache'e kaydedildi!\n\nDosya: ${file.name}\nOrijinal boyut: ${response.data.original_size} byte\nMarkdown boyutu: ${response.data.markdown_size} karakter\n\n▶️ Bir sonraki işlemde daha hızlı yüklenecek.`
            );
          }, 500);
        }
      } else {
        throw new Error('Dosya işleme hatası');
      }
    } catch (error) {
      alert('Dosya işlenirken bir hata oluştu. Lütfen metni manuel olarak yapıştırın.');

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
                onChange: (option: any) => setDomain(option?.value || ''),
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
