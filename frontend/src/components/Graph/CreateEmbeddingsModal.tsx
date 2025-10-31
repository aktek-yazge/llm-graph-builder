import { Banner, Button, Checkbox, Dialog, Typography } from '@neo4j-ndl/react';
import React, { useState } from 'react';
import { useCredentials } from '../../context/UserCredentials';
import { useFileContext } from '../../context/UsersFiles';
import { showErrorToast, showSuccessToast } from '../../utils/Toasts';

interface CreateEmbeddingsModalProps {
  open: boolean;
  onClose: () => void;
}

const CreateEmbeddingsModal: React.FC<CreateEmbeddingsModalProps> = ({ open, onClose }) => {
  const { userCredentials } = useCredentials();
  const { filesData } = useFileContext();
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  // Available files from context - filter undefined values
  const availableFiles = filesData?.map((file) => file.name).filter((name): name is string => Boolean(name)) || [];

  const handleFileSelection = (fileName: string, checked: boolean) => {
    if (checked) {
      setSelectedFiles((prev) => [...prev, fileName]);
    } else {
      setSelectedFiles((prev) => prev.filter((name) => name !== fileName));
    }
  };

  const handleSelectAll = (checked: boolean) => {
    if (checked) {
      setSelectedFiles([...availableFiles]);
    } else {
      setSelectedFiles([]);
    }
  };

  const handleCreateEmbeddings = async () => {
    if (selectedFiles.length === 0) {
      showErrorToast('En az bir dosya seçiniz');
      return;
    }

    setIsLoading(true);
    setResult(null);

    try {
      const formData = new FormData();
      formData.append('uri', userCredentials?.uri || '');
      formData.append('userName', userCredentials?.userName || '');
      formData.append('password', userCredentials?.password || '');
      formData.append('database', userCredentials?.database || '');
      formData.append('file_names', JSON.stringify(selectedFiles));

      const response = await fetch(`${import.meta.env.VITE_BACKEND_API_URL}/create_embeddings`, {
        method: 'POST',
        body: formData,
      });

      const data = await response.json();

      if (data.status === 'Success') {
        setResult(data.data);
        showSuccessToast(data.message || 'Embedding oluşturma başarılı');
      } else {
        throw new Error(data.error || data.message || 'Embedding oluşturma başarısız');
      }
    } catch (error: any) {
      showErrorToast(`Embedding oluşturma hatası: ${error.message || 'Bilinmeyen hata'}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClose = () => {
    setSelectedFiles([]);
    setResult(null);
    onClose();
  };

  return (
    <Dialog isOpen={open} onClose={handleClose} size='large'>
      <Dialog.Header>
        <Typography variant='h4'>📊 Chunk Embeddings Oluştur</Typography>
      </Dialog.Header>

      <Dialog.Content>
        {!result ? (
          <>
            <Typography variant='body-medium' className='mb-4'>
              Seçilen dosyalar için chunk node'larına embedding oluşturulacak. Bu işlem chunk'ların vector similarity
              aramaları için gereklidir.
            </Typography>

            {availableFiles.length === 0 ? (
              <Banner
                type='info'
                title='Dosya Bulunamadı'
                description='Henüz yüklenmiş dosya bulunmuyor. Önce dosya yükleyiniz.'
              />
            ) : (
              <>
                <div className='mb-4'>
                  <Checkbox
                    label={`Tümünü Seç (${availableFiles.length} dosya)`}
                    isChecked={selectedFiles.length === availableFiles.length}
                    onChange={(e) => handleSelectAll(e.target.checked)}
                    isDisabled={isLoading}
                  />
                </div>

                <div className='max-h-64 overflow-y-auto border rounded p-2'>
                  {availableFiles.map((fileName) => (
                    <div key={fileName} className='mb-2'>
                      <Checkbox
                        label={fileName}
                        isChecked={selectedFiles.includes(fileName)}
                        onChange={(e) => handleFileSelection(fileName, e.target.checked)}
                        isDisabled={isLoading}
                      />
                    </div>
                  ))}
                </div>

                <Typography variant='body-small' className='mt-2 text-gray-600'>
                  Seçilen dosyalar: {selectedFiles.length} / {availableFiles.length}
                </Typography>
              </>
            )}
          </>
        ) : (
          <div>
            <Banner
              type='success'
              title='Embedding Oluşturma Tamamlandı'
              description={`${result.total_chunks_updated || 0} chunk için embedding oluşturuldu`}
            />

            <div className='mt-4 p-3 bg-gray-50 rounded'>
              <Typography variant='subheading-small' className='mb-2'>
                📊 İşlem Özeti:
              </Typography>
              <ul className='list-disc list-inside space-y-1 text-sm'>
                <li>Toplam Dosya: {result.total_files || 0}</li>
                <li>İşlenen Chunk: {result.total_chunks_processed || 0}</li>
                <li>Oluşturulan Embedding: {result.total_chunks_updated || 0}</li>
                <li>Embedding Model: {result.embedding_model || 'Bilinmiyor'}</li>
                <li>Embedding Boyutu: {result.embedding_dimension || 0}</li>
              </ul>
            </div>

            {result.files && Object.keys(result.files).length > 0 && (
              <div className='mt-4'>
                <Typography variant='subheading-small' className='mb-2'>
                  📁 Dosya Detayları:
                </Typography>
                <div className='max-h-48 overflow-y-auto'>
                  {Object.entries(result.files).map(([fileName, fileResult]: [string, any]) => (
                    <div key={fileName} className='mb-2 p-2 border rounded'>
                      <Typography variant='body-small' className='font-medium'>
                        {fileName}
                      </Typography>
                      <Typography variant='body-small' className='text-gray-600'>
                        {fileResult.status === 'success' && `✅ ${fileResult.chunks_updated} embedding oluşturuldu`}
                        {fileResult.status === 'skipped' && `⏭️ ${fileResult.message}`}
                        {fileResult.status === 'error' && `❌ ${fileResult.message}`}
                      </Typography>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </Dialog.Content>

      <Dialog.Actions>
        {!result ? (
          <>
            <Button onClick={handleClose} fill='outlined' size='medium' isDisabled={isLoading}>
              İptal
            </Button>
            <Button
              onClick={handleCreateEmbeddings}
              fill='filled'
              size='medium'
              isLoading={isLoading}
              isDisabled={selectedFiles.length === 0 || isLoading}
            >
              {isLoading ? 'Oluşturuluyor...' : `Embedding Oluştur (${selectedFiles.length} dosya)`}
            </Button>
          </>
        ) : (
          <Button onClick={handleClose} fill='filled' size='medium'>
            Kapat
          </Button>
        )}
      </Dialog.Actions>
    </Dialog>
  );
};

export default CreateEmbeddingsModal;
