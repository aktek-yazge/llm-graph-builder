import { Banner, Button, Checkbox, Dialog, Typography } from '@neo4j-ndl/react';
import React, { useState } from 'react';
import { useCredentials } from '../../context/UserCredentials';
import { showErrorToast, showSuccessToast } from '../../utils/Toasts';

interface MergeDuplicateEntitiesModalProps {
  open: boolean;
  onClose: () => void;
}

interface MergeResults {
  customers?: number;
  insurance_companies?: number;
  coverage_types?: number;
  total_merged: number;
  error?: string;
}

const MergeDuplicateEntitiesModal: React.FC<MergeDuplicateEntitiesModalProps> = ({ open, onClose }) => {
  const [selectedNodeTypes, setSelectedNodeTypes] = useState<string[]>(['all']);
  const [isLoading, setIsLoading] = useState(false);
  const [mergeResults, setMergeResults] = useState<MergeResults | null>(null);
  const { userCredentials } = useCredentials();

  const nodeTypeOptions = [
    { value: 'all', label: 'Tüm Node Türleri', description: 'Customers, Insurance Companies ve Coverage Types' },
    { value: 'customers', label: 'Customers', description: "Müşteri duplicate node'ları" },
    { value: 'insurance_companies', label: 'Insurance Companies', description: "Sigorta şirketi duplicate node'ları" },
    { value: 'coverage_types', label: 'Coverage Types', description: "Teminat türü duplicate node'ları" },
  ];

  const handleNodeTypeChange = (nodeType: string, event: React.ChangeEvent<HTMLInputElement>) => {
    const { checked } = event.target;
    if (nodeType === 'all') {
      if (checked) {
        setSelectedNodeTypes(['all']);
      } else {
        setSelectedNodeTypes([]);
      }
    } else {
      setSelectedNodeTypes((prev) => {
        const newSelection = prev.filter((type) => type !== 'all');
        if (checked) {
          return [...newSelection, nodeType];
        }
        return newSelection.filter((type) => type !== nodeType);
      });
    }
  };

  const handleMergeEntities = async () => {
    if (!userCredentials || !userCredentials.uri) {
      showErrorToast('Lütfen Neo4j bağlantısını kontrol edin.');
      return;
    }

    if (selectedNodeTypes.length === 0) {
      showErrorToast('Lütfen en az bir node türü seçin');
      return;
    }

    setIsLoading(true);
    setMergeResults(null);

    try {
      const formData = new FormData();
      formData.append('uri', userCredentials.uri || '');
      formData.append('node_types', JSON.stringify(selectedNodeTypes));
      if (userCredentials.email) {
        formData.append('email', userCredentials.email);
      }

      const response = await fetch(`${import.meta.env.VITE_BACKEND_API_URL}/merge_duplicate_entities`, {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();

      if (result.status === 'Success') {
        setMergeResults(result.data);
        showSuccessToast(result.message || 'Duplicate entities başarıyla merge edildi');
      } else {
        throw new Error(result.error || result.message || 'Merge işlemi başarısız');
      }
    } catch (error) {
      showErrorToast(error instanceof Error ? error.message : 'Merge işlemi sırasında hata oluştu');
    } finally {
      setIsLoading(false);
    }
  };

  const handleClose = () => {
    setMergeResults(null);
    setSelectedNodeTypes(['all']);
    onClose();
  };

  return (
    <Dialog
      size='medium'
      isOpen={open}
      onClose={handleClose}
      htmlAttributes={{ 'aria-labelledby': 'merge-duplicate-entities-title' }}
    >
      <Dialog.Header>Duplicate Entities Merge</Dialog.Header>

      <Dialog.Content className='n-flex n-flex-col n-gap-token-4'>
        <Typography variant='body-medium'>
          Bu işlem seçilen node türlerindeki duplicate (benzer) entity'leri otomatik olarak birleştirir. Birleştirme
          işlemi text similarity algoritmaları kullanılarak yapılır.
        </Typography>

        {mergeResults && (
          <Banner type={mergeResults.error ? 'danger' : 'success'}>
            {mergeResults.error ? (
              <Typography variant='body-medium'>❌ Hata: {mergeResults.error}</Typography>
            ) : (
              <div className='n-flex n-flex-col n-gap-token-2'>
                <Typography variant='h6'>🎉 Merge İşlemi Tamamlandı</Typography>
                <Typography variant='body-medium'>Toplam {mergeResults.total_merged} node birleştirildi:</Typography>
                <ul style={{ marginLeft: '20px' }}>
                  {mergeResults.customers !== undefined && <li>Customers: {mergeResults.customers}</li>}
                  {mergeResults.insurance_companies !== undefined && (
                    <li>Insurance Companies: {mergeResults.insurance_companies}</li>
                  )}
                  {mergeResults.coverage_types !== undefined && <li>Coverage Types: {mergeResults.coverage_types}</li>}
                </ul>
              </div>
            )}
          </Banner>
        )}

        <div>
          <Typography variant='h6' style={{ marginBottom: '12px' }}>
            Merge Edilecek Node Türleri:
          </Typography>

          <div className='n-flex n-flex-col n-gap-token-3'>
            {nodeTypeOptions.map((option) => (
              <div key={option.value} className='n-flex n-flex-row n-items-start n-gap-token-3'>
                <Checkbox
                  label={option.label}
                  isChecked={
                    option.value === 'all'
                      ? selectedNodeTypes.includes('all')
                      : selectedNodeTypes.includes(option.value)
                  }
                  onChange={(event) => handleNodeTypeChange(option.value, event)}
                  isDisabled={isLoading}
                />
                <div>
                  <Typography variant='body-small' style={{ color: '#666', marginTop: '2px' }}>
                    {option.description}
                  </Typography>
                </div>
              </div>
            ))}
          </div>
        </div>

        {selectedNodeTypes.length > 0 && (
          <Banner type='info'>
            <Typography variant='body-small'>
              ℹ️ Seçilen türler: {selectedNodeTypes.includes('all') ? 'Tüm türler' : selectedNodeTypes.join(', ')}
            </Typography>
          </Banner>
        )}
      </Dialog.Content>

      <Dialog.Actions>
        <Button fill='outlined' onClick={handleClose} isDisabled={isLoading}>
          Kapat
        </Button>
        <Button
          onClick={handleMergeEntities}
          isDisabled={isLoading || selectedNodeTypes.length === 0}
          isLoading={isLoading}
        >
          {isLoading ? 'Merge Ediliyor...' : 'Duplicate Entities Merge Et'}
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
};

export default MergeDuplicateEntitiesModal;
