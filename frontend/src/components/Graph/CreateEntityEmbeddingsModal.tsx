import { Banner, Button, Checkbox, Dialog, Typography } from '@neo4j-ndl/react';
import React, { useEffect, useState } from 'react';
import { useCredentials } from '../../context/UserCredentials';
import { showErrorToast, showSuccessToast } from '../../utils/Toasts';

interface CreateEntityEmbeddingsModalProps {
  open: boolean;
  onClose: () => void;
}

const CreateEntityEmbeddingsModal: React.FC<CreateEntityEmbeddingsModalProps> = ({ open, onClose }) => {
  const { userCredentials } = useCredentials();
  const [availableNodeTypes, setAvailableNodeTypes] = useState<string[]>([]);
  const [selectedNodeTypes, setSelectedNodeTypes] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  // Get available node types when modal opens
  useEffect(() => {
    if (open && userCredentials) {
      fetchAvailableNodeTypes();
    }
  }, [open, userCredentials]);

  const fetchAvailableNodeTypes = () => {
    try {
      // We'll get available types from the backend when we call the API
      // For now, set some common entity types
      const commonTypes = [
        'Customer',
        'Policy',
        'CoverageType',
        'Premium',
        'Coverage',
        'Guarantee',
        'Clause',
        'RiskAddress',
        'InsuranceCompany',
        'Date',
      ];
      setAvailableNodeTypes(commonTypes);
    } catch (error) {
      setAvailableNodeTypes([]);
    }
  };

  const handleNodeTypeSelection = (nodeType: string, checked: boolean) => {
    if (checked) {
      setSelectedNodeTypes((prev) => [...prev, nodeType]);
    } else {
      setSelectedNodeTypes((prev) => prev.filter((type) => type !== nodeType));
    }
  };

  const handleSelectAll = (checked: boolean) => {
    if (checked) {
      setSelectedNodeTypes([...availableNodeTypes]);
    } else {
      setSelectedNodeTypes([]);
    }
  };

  const handleCreateEntityEmbeddings = async () => {
    if (selectedNodeTypes.length === 0) {
      showErrorToast('En az bir node türü seçiniz');
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
      formData.append('node_types', JSON.stringify(selectedNodeTypes));

      const response = await fetch(`${import.meta.env.VITE_BACKEND_API_URL}/create_entity_embeddings`, {
        method: 'POST',
        body: formData,
      });

      const data = await response.json();

      if (data.status === 'Success') {
        setResult(data.data);
        showSuccessToast(data.message || 'Entity embedding oluşturma başarılı');

        // Update available types from response
        if (data.data.available_types) {
          setAvailableNodeTypes(data.data.available_types);
        }
      } else {
        throw new Error(data.error || data.message || 'Entity embedding oluşturma başarısız');
      }
    } catch (error: any) {
      showErrorToast(`Entity embedding oluşturma hatası: ${error.message || 'Bilinmeyen hata'}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClose = () => {
    setSelectedNodeTypes([]);
    setResult(null);
    onClose();
  };

  return (
    <Dialog isOpen={open} onClose={handleClose} size='large'>
      <Dialog.Header>
        <Typography variant='h4'>🧠 Entity Embeddings Oluştur</Typography>
      </Dialog.Header>

      <Dialog.Content>
        {!result ? (
          <>
            <Typography variant='body-medium' className='mb-4'>
              Seçilen entity node türleri için embedding oluşturulacak. Bu işlem entity'ler arasında semantic similarity
              aramaları için gereklidir.
            </Typography>

            {availableNodeTypes.length === 0 ? (
              <Banner
                type='info'
                title='Node Türü Bulunamadı'
                description="Henüz entity node'ları bulunmuyor. Önce dosya işleme (extract) yapınız."
              />
            ) : (
              <>
                <div className='mb-4'>
                  <Checkbox
                    label={`Tümünü Seç (${availableNodeTypes.length} tür)`}
                    isChecked={selectedNodeTypes.length === availableNodeTypes.length}
                    onChange={(e) => handleSelectAll(e.target.checked)}
                    isDisabled={isLoading}
                  />
                </div>

                <div className='max-h-64 overflow-y-auto border rounded p-2'>
                  {availableNodeTypes.map((nodeType) => (
                    <div key={nodeType} className='mb-2'>
                      <Checkbox
                        label={nodeType}
                        isChecked={selectedNodeTypes.includes(nodeType)}
                        onChange={(e) => handleNodeTypeSelection(nodeType, e.target.checked)}
                        isDisabled={isLoading}
                      />
                    </div>
                  ))}
                </div>

                <Typography variant='body-small' className='mt-2 text-gray-600'>
                  Seçilen node türleri: {selectedNodeTypes.length} / {availableNodeTypes.length}
                </Typography>
              </>
            )}
          </>
        ) : (
          <div>
            <Banner
              type='success'
              title='Entity Embedding Oluşturma Tamamlandı'
              description={`${result.total_embeddings_created || 0} entity için embedding oluşturuldu`}
            />

            <div className='mt-4 p-3 bg-gray-50 rounded'>
              <Typography variant='subheading-small' className='mb-2'>
                📊 İşlem Özeti:
              </Typography>
              <ul className='list-disc list-inside space-y-1 text-sm'>
                <li>Toplam Node Türü: {result.total_node_types || 0}</li>
                <li>İşlenen Entity: {result.total_entities_processed || 0}</li>
                <li>Oluşturulan Embedding: {result.total_embeddings_created || 0}</li>
                <li>Embedding Model: {result.embedding_model || 'Bilinmiyor'}</li>
                <li>Embedding Boyutu: {result.embedding_dimension || 0}</li>
              </ul>
            </div>

            {result.node_types && Object.keys(result.node_types).length > 0 && (
              <div className='mt-4'>
                <Typography variant='subheading-small' className='mb-2'>
                  🏷️ Node Türü Detayları:
                </Typography>
                <div className='max-h-48 overflow-y-auto'>
                  {Object.entries(result.node_types).map(([nodeType, typeResult]: [string, any]) => (
                    <div key={nodeType} className='mb-2 p-2 border rounded'>
                      <Typography variant='body-small' className='font-medium'>
                        {nodeType}
                      </Typography>
                      <Typography variant='body-small' className='text-gray-600'>
                        {typeResult.status === 'success' && `✅ ${typeResult.entities_updated} embedding oluşturuldu`}
                        {typeResult.status === 'skipped' && `⏭️ ${typeResult.message}`}
                        {typeResult.status === 'error' && `❌ ${typeResult.message}`}
                      </Typography>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {result.available_types && (
              <div className='mt-4 p-3 bg-blue-50 rounded'>
                <Typography variant='subheading-small' className='mb-2'>
                  🗂️ Mevcut Node Türleri:
                </Typography>
                <Typography variant='body-small' className='text-gray-700'>
                  {result.available_types.join(', ')}
                </Typography>
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
              onClick={handleCreateEntityEmbeddings}
              fill='filled'
              size='medium'
              isLoading={isLoading}
              isDisabled={selectedNodeTypes.length === 0 || isLoading}
            >
              {isLoading ? 'Oluşturuluyor...' : `Entity Embedding Oluştur (${selectedNodeTypes.length} tür)`}
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

export default CreateEntityEmbeddingsModal;
