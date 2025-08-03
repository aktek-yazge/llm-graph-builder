import { useState, useCallback } from 'react';
import { Button, Typography, Select, Flex, Banner } from '@neo4j-ndl/react';
import { PlusIconOutline, TrashIconOutline, PlayIconOutline } from '@neo4j-ndl/react/icons';
import { useCredentials } from '../../context/UserCredentials';
import { CustomFile } from '../../types';

interface PostProcessingRule {
  id: string;
  source_node_type: string;
  target_node_type: string;
  relationship_types: string[];
  target_selection: 'document' | 'specific_target';
}

interface EntityRelationshipPostProcessingProps {
  selectedFiles: CustomFile[];
  onSuccess?: (message: string) => void;
  onError?: (error: string) => void;
}

export default function EntityRelationshipPostProcessing({
  selectedFiles,
  onSuccess,
  onError,
}: EntityRelationshipPostProcessingProps) {
  const { userCredentials } = useCredentials();
  const [rules, setRules] = useState<PostProcessingRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [results, setResults] = useState<any>(null);
  const [newRule, setNewRule] = useState<Partial<PostProcessingRule>>({
    source_node_type: '',
    target_node_type: '',
    relationship_types: [],
    target_selection: 'document',
  });

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

  const targetSelectionOptions = [
    { label: "Document Node'a Bağla", value: 'document' },
    { label: 'Spesifik Hedeflere Bağla', value: 'specific_target' },
  ];

  const addRule = useCallback(() => {
    if (!newRule.source_node_type || !newRule.target_node_type || !newRule.relationship_types?.length) {
      return;
    }

    const rule: PostProcessingRule = {
      id: Date.now().toString(),
      source_node_type: newRule.source_node_type!,
      target_node_type: newRule.target_node_type!,
      relationship_types: newRule.relationship_types!,
      target_selection: newRule.target_selection || 'document',
    };

    setRules((prev) => [...prev, rule]);
    setNewRule({
      source_node_type: '',
      target_node_type: '',
      relationship_types: [],
      target_selection: 'document',
    });
  }, [newRule]);

  const removeRule = useCallback((ruleId: string) => {
    setRules((prev) => prev.filter((rule) => rule.id !== ruleId));
  }, []);

  const executePostProcessing = useCallback(async () => {
    if (!rules.length) {
      return;
    }

    if (!selectedFiles.length) {
      return;
    }

    if (!userCredentials) {
      onError?.('Kullanıcı kimlik bilgileri bulunamadı');
      return;
    }

    setLoading(true);
    try {
      const fileNames = selectedFiles.map((file) => file.name);

      const formData = new FormData();
      formData.append('uri', userCredentials.uri || '');
      formData.append('userName', userCredentials.userName || '');
      formData.append('password', userCredentials.password || '');
      formData.append('database', userCredentials.database || '');
      formData.append('file_names', JSON.stringify(fileNames));
      formData.append('post_processing_rules', JSON.stringify(rules));
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
  }, [rules, selectedFiles, userCredentials, onSuccess, onError]);

  if (!selectedFiles.length) {
    return (
      <div style={{ margin: '20px 0' }}>
        <Banner type='info' description='Entity ilişki post-processing yapmak için dosya seçin.' />
      </div>
    );
  }

  return (
    <div style={{ margin: '20px 0', padding: '20px', border: '1px solid #d9d9d9', borderRadius: '8px' }}>
      <Typography variant='h4' style={{ marginBottom: '16px' }}>
        Entity İlişki Post-Processing
      </Typography>

      <Typography variant='body-medium' style={{ marginBottom: '20px', color: '#666' }}>
        Seçili dosyalardaki entity'leri belirtilen kurallara göre diğer node'lara bağlar.
      </Typography>

      <div style={{ marginBottom: '20px' }}>
        <Typography variant='h6' style={{ marginBottom: '8px' }}>
          Seçili Dosyalar ({selectedFiles.length})
        </Typography>
        <Typography variant='body-small' style={{ color: '#666' }}>
          {selectedFiles.map((f) => f.name).join(', ')}
        </Typography>
      </div>

      {/* Yeni Kural Ekleme Formu */}
      <div
        style={{
          marginBottom: '20px',
          padding: '16px',
          backgroundColor: '#f5f5f5',
          borderRadius: '8px',
        }}
      >
        <Typography variant='h6' style={{ marginBottom: '16px' }}>
          Yeni Kural Ekle
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
                  value: commonNodeTypes.find((opt) => opt.value === newRule.source_node_type),
                  onChange: (option: any) =>
                    setNewRule((prev) => ({
                      ...prev,
                      source_node_type: option?.value || '',
                    })),
                  placeholder: 'Kaynak node tipini seçin',
                  isSearchable: true,
                  isClearable: true,
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
                  value: commonNodeTypes.find((opt) => opt.value === newRule.target_node_type),
                  onChange: (option: any) =>
                    setNewRule((prev) => ({
                      ...prev,
                      target_node_type: option?.value || '',
                    })),
                  placeholder: 'Hedef node tipini seçin',
                  isSearchable: true,
                  isClearable: true,
                }}
              />
            </div>
          </Flex>

          <Flex gap='4'>
            <div style={{ flex: 1 }}>
              <Typography variant='body-medium' style={{ marginBottom: '8px' }}>
                İlişki Tipleri
              </Typography>
              <Select
                type='select'
                selectProps={{
                  options: commonRelationshipTypes,
                  value: commonRelationshipTypes.filter((opt) => newRule.relationship_types?.includes(opt.value)),
                  onChange: (options: any) =>
                    setNewRule((prev) => ({
                      ...prev,
                      relationship_types: options ? options.map((opt: any) => opt.value) : [],
                    })),
                  placeholder: 'İlişki tiplerini seçin',
                  isSearchable: true,
                  isMulti: true,
                }}
              />
            </div>

            <div style={{ flex: 1 }}>
              <Typography variant='body-medium' style={{ marginBottom: '8px' }}>
                Hedef Seçimi
              </Typography>
              <Select
                type='select'
                selectProps={{
                  options: targetSelectionOptions,
                  value: targetSelectionOptions.find((opt) => opt.value === newRule.target_selection),
                  onChange: (option: any) =>
                    setNewRule((prev) => ({
                      ...prev,
                      target_selection: option?.value || 'document',
                    })),
                  placeholder: 'Hedef seçimi',
                }}
              />
            </div>
          </Flex>

          <div style={{ textAlign: 'right' }}>
            <Button
              color='primary'
              onClick={addRule}
              isDisabled={!newRule.source_node_type || !newRule.target_node_type || !newRule.relationship_types?.length}
            >
              <PlusIconOutline />
              Kural Ekle
            </Button>
          </div>
        </Flex>
      </div>

      {/* Tanımlı Kurallar Tablosu */}
      {rules.length > 0 && (
        <div style={{ marginBottom: '20px' }}>
          <Typography variant='h6' style={{ marginBottom: '16px' }}>
            Tanımlı Kurallar
          </Typography>
          <div style={{ border: '1px solid #d9d9d9', borderRadius: '8px', overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead style={{ backgroundColor: '#f5f5f5' }}>
                <tr>
                  <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #d9d9d9' }}>
                    Kaynak Node Tipi
                  </th>
                  <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #d9d9d9' }}>
                    Hedef Node Tipi
                  </th>
                  <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #d9d9d9' }}>
                    İlişki Tipleri
                  </th>
                  <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #d9d9d9' }}>
                    Hedef Seçimi
                  </th>
                  <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #d9d9d9' }}>İşlemler</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.id}>
                    <td style={{ padding: '12px', borderBottom: '1px solid #d9d9d9' }}>{rule.source_node_type}</td>
                    <td style={{ padding: '12px', borderBottom: '1px solid #d9d9d9' }}>{rule.target_node_type}</td>
                    <td style={{ padding: '12px', borderBottom: '1px solid #d9d9d9' }}>
                      {rule.relationship_types.join(', ')}
                    </td>
                    <td style={{ padding: '12px', borderBottom: '1px solid #d9d9d9' }}>
                      {rule.target_selection === 'document' ? 'Document' : 'Spesifik Hedef'}
                    </td>
                    <td style={{ padding: '12px', borderBottom: '1px solid #d9d9d9' }}>
                      <Button color='danger' fill='text' size='small' onClick={() => removeRule(rule.id)}>
                        <TrashIconOutline />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Sonuçlar */}
      {showResults && results && (
        <div style={{ marginBottom: '20px' }}>
          <Typography variant='h6' style={{ marginBottom: '16px' }}>
            Post-Processing Sonuçları
          </Typography>
          <div
            style={{
              padding: '16px',
              backgroundColor: '#f0f9ff',
              borderRadius: '8px',
              border: '1px solid #0ea5e9',
            }}
          >
            <Typography variant='body-medium'>
              <strong>İşlenen Dosyalar:</strong> {results.processed_files}
            </Typography>
            <Typography variant='body-medium'>
              <strong>Uygulanan Kurallar:</strong> {results.applied_rules}
            </Typography>
            <Typography variant='body-medium'>
              <strong>İşlenen Entity'ler:</strong> {results.total_processed_entities}
            </Typography>
            <Typography variant='body-medium'>
              <strong>Oluşturulan İlişkiler:</strong> {results.total_created_relationships}
            </Typography>
            <Typography variant='body-medium'>
              <strong>Süre:</strong> {results.elapsed_api_time || 'N/A'} saniye
            </Typography>
          </div>
        </div>
      )}

      {/* Çalıştır Butonu */}
      <Flex justifyContent='flex-end'>
        <Button
          color='primary'
          onClick={executePostProcessing}
          isLoading={loading}
          isDisabled={!rules.length || !selectedFiles.length}
        >
          <PlayIconOutline />
          Post-Processing Çalıştır
        </Button>
      </Flex>
    </div>
  );
}
