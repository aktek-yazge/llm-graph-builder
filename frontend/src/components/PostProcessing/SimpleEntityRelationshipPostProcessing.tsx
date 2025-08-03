import { useState, useCallback } from 'react';
import { Button, Typography, Select, Flex, Checkbox } from '@neo4j-ndl/react';
import { PlayIconOutline } from '@neo4j-ndl/react/icons';
import { useCredentials } from '../../context/UserCredentials';
import { CustomFile } from '../../types';

interface SimpleEntityRelationshipPostProcessingProps {
  selectedFiles: CustomFile[];
  onSuccess?: (message: string) => void;
  onError?: (error: string) => void;
}

export default function SimpleEntityRelationshipPostProcessing({
  selectedFiles,
  onSuccess,
  onError,
}: SimpleEntityRelationshipPostProcessingProps) {
  const { userCredentials } = useCredentials();
  const [loading, setLoading] = useState(false);
  const [sourceNodeType, setSourceNodeType] = useState('Year');
  const [targetNodeType, setTargetNodeType] = useState('Document');
  const [relationshipType, setRelationshipType] = useState('OCCURS_IN');
  const [removeExistingRelationships, setRemoveExistingRelationships] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [results, setResults] = useState<any>(null);

  // Yaygın node tipleri
  const commonNodeTypes = [
    { label: 'Year', value: 'Year' },
    { label: 'Date', value: 'Date' },
    { label: 'Person', value: 'Person' },
    { label: 'Company', value: 'Company' },
    { label: 'Location', value: 'Location' },
    { label: 'Address', value: 'Address' },
    { label: 'PolicyNumber', value: 'PolicyNumber' },
    { label: 'IdentityNumber', value: 'IdentityNumber' },
    { label: 'PlateNumber', value: 'PlateNumber' },
    { label: 'Amount', value: 'Amount' },
    { label: 'DateRange', value: 'DateRange' },
    { label: 'Document', value: 'Document' },
    { label: 'Policy', value: 'Policy' },
    { label: 'Coverage', value: 'Coverage' },
    { label: 'Vehicle', value: 'Vehicle' },
    { label: 'Property', value: 'Property' },
    { label: 'InsuranceCompany', value: 'InsuranceCompany' },
    { label: 'PolicyHolder', value: 'PolicyHolder' },
  ];

  // Yaygın relationship tipleri
  const commonRelationshipTypes = [
    { label: 'OCCURS_IN', value: 'OCCURS_IN' },
    { label: 'HAS_YEAR', value: 'HAS_YEAR' },
    { label: 'HAS_DATE', value: 'HAS_DATE' },
    { label: 'BELONGS_TO', value: 'BELONGS_TO' },
    { label: 'LOCATED_IN', value: 'LOCATED_IN' },
    { label: 'WORKS_FOR', value: 'WORKS_FOR' },
    { label: 'OWNS', value: 'OWNS' },
    { label: 'ISSUED_BY', value: 'ISSUED_BY' },
    { label: 'COVERS', value: 'COVERS' },
    { label: 'INSURED_BY', value: 'INSURED_BY' },
    { label: 'RELATED_TO', value: 'RELATED_TO' },
    { label: 'PART_OF', value: 'PART_OF' },
  ];

  const executePostProcessing = useCallback(async () => {
    if (!selectedFiles.length) {
      onError?.('İşlenecek dosya bulunamadı');
      return;
    }

    if (!userCredentials) {
      onError?.('Kullanıcı kimlik bilgileri bulunamadı');
      return;
    }

    setLoading(true);
    try {
      const fileNames = selectedFiles.map((file) => file.name);

      // Basit kural oluştur
      const rule = {
        id: '1',
        source_node_type: sourceNodeType,
        target_node_type: targetNodeType,
        relationship_types: [relationshipType],
        target_selection: 'document',
        remove_existing_relationships: removeExistingRelationships,
      };

      const formData = new FormData();
      formData.append('uri', userCredentials.uri || '');
      formData.append('userName', userCredentials.userName || '');
      formData.append('password', userCredentials.password || '');
      formData.append('database', userCredentials.database || '');
      formData.append('file_names', JSON.stringify(fileNames));
      formData.append('post_processing_rules', JSON.stringify([rule]));
      formData.append('email', userCredentials.email || '');

      const response = await fetch('/entity_relationship_post_processing', {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();

      if (result.status === 'Success') {
        const successMessage = `Post-processing tamamlandı! ${result.data.total_created_relationships} relationship oluşturuldu.`;
        onSuccess?.(successMessage);
        setResults(result.data);
        setShowResults(true);
      } else {
        const errorMessage = `Hata: ${result.message}`;
        onError?.(errorMessage);
      }
    } catch (error) {
      console.error('Post-processing error:', error);
      const errorMessage = 'Post-processing işlemi sırasında hata oluştu';
      onError?.(errorMessage);
    } finally {
      setLoading(false);
    }
  }, [
    sourceNodeType,
    targetNodeType,
    relationshipType,
    removeExistingRelationships,
    selectedFiles,
    userCredentials,
    onSuccess,
    onError,
  ]);

  if (!selectedFiles.length) {
    return null;
  }

  return (
    <div
      style={{
        marginTop: '24px',
        padding: '20px',
        backgroundColor: '#f8f9fa',
        border: '1px solid #e9ecef',
        borderRadius: '8px',
      }}
    >
      <Typography variant='h6' style={{ marginBottom: '16px' }}>
        Entity İlişki Post-Processing
      </Typography>

      <Typography variant='body-medium' style={{ marginBottom: '20px', color: '#666' }}>
        Tüm işlenmiş dosyalardaki seçili entity tipini belirtilen node tipine bağlar. ({selectedFiles.length} dosya)
      </Typography>

      <Flex flexDirection='column' gap='4'>
        <Flex gap='4'>
          <div style={{ flex: 1 }}>
            <Typography variant='body-medium' style={{ marginBottom: '8px' }}>
              Kaynak Node Tipi
            </Typography>
            <Select
              type='select'
              selectProps={{
                options: commonNodeTypes,
                value: commonNodeTypes.find((opt) => opt.value === sourceNodeType),
                onChange: (option: any) => setSourceNodeType(option?.value || 'Year'),
                placeholder: 'Kaynak node tipini seçin',
                isSearchable: true,
              }}
            />
          </div>

          <div style={{ flex: 1 }}>
            <Typography variant='body-medium' style={{ marginBottom: '8px' }}>
              Hedef Node Tipi
            </Typography>
            <Select
              type='select'
              selectProps={{
                options: commonNodeTypes,
                value: commonNodeTypes.find((opt) => opt.value === targetNodeType),
                onChange: (option: any) => setTargetNodeType(option?.value || 'Document'),
                placeholder: 'Hedef node tipini seçin',
                isSearchable: true,
              }}
            />
          </div>

          <div style={{ flex: 1 }}>
            <Typography variant='body-medium' style={{ marginBottom: '8px' }}>
              İlişki Tipi
            </Typography>
            <Select
              type='select'
              selectProps={{
                options: commonRelationshipTypes,
                value: commonRelationshipTypes.find((opt) => opt.value === relationshipType),
                onChange: (option: any) => setRelationshipType(option?.value || 'OCCURS_IN'),
                placeholder: 'İlişki tipini seçin',
                isSearchable: true,
              }}
            />
          </div>
        </Flex>

        <div style={{ marginTop: '12px' }}>
          <Checkbox
            label={<Typography variant='body-medium'>Mevcut ilişkileri sil ve yenilerini oluştur</Typography>}
            isChecked={removeExistingRelationships}
            onChange={(e) => setRemoveExistingRelationships(e.target.checked)}
            ariaLabel='remove-existing-relationships'
          />
        </div>

        {/* Sonuçlar */}
        {showResults && results && (
          <div
            style={{
              marginTop: '16px',
              padding: '16px',
              backgroundColor: '#d1ecf1',
              borderRadius: '8px',
              border: '1px solid #bee5eb',
            }}
          >
            <Typography variant='h6' style={{ marginBottom: '12px' }}>
              Post-Processing Sonuçları
            </Typography>
            <Typography variant='body-small'>
              <strong>İşlenen Dosyalar:</strong> {results.processed_files}
            </Typography>
            <Typography variant='body-small'>
              <strong>İşlenen Entity'ler:</strong> {results.total_processed_entities}
            </Typography>
            <Typography variant='body-small'>
              <strong>Oluşturulan İlişkiler:</strong> {results.total_created_relationships}
            </Typography>
            <Typography variant='body-small'>
              <strong>Süre:</strong> {results.elapsed_api_time || 'N/A'} saniye
            </Typography>
          </div>
        )}

        <Flex justifyContent='flex-end' style={{ marginTop: '16px' }}>
          <Button
            color='primary'
            onClick={executePostProcessing}
            isLoading={loading}
            isDisabled={!selectedFiles.length}
          >
            <PlayIconOutline />
            Post-Processing Uygula
          </Button>
        </Flex>
      </Flex>
    </div>
  );
}
