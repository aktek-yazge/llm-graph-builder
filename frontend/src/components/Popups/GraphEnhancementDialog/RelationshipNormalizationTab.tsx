import { useState, useCallback } from 'react';
import {
  Button,
  Typography,
  Flex,
  Banner,
  DataGrid,
  DataGridComponents,
  Checkbox,
  TextInput,
  StatusIndicator,
} from '@neo4j-ndl/react';
import { PlayIconSolid, ArrowPathIconOutline } from '@neo4j-ndl/react/icons';
import { useCredentials } from '../../../context/UserCredentials';
import {
  getRelationshipNormalizationPreview,
  applyRelationshipNormalization,
  NormalizationGroup,
  NormalizationPreview,
} from '../../../services/RelationshipNormalization';

interface EditableGroup extends NormalizationGroup {
  selected: boolean;
  editedName: string;
}

export default function RelationshipNormalizationTab() {
  const { userCredentials } = useCredentials();
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [preview, setPreview] = useState<NormalizationPreview | null>(null);
  const [groups, setGroups] = useState<EditableGroup[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const handleAnalyze = useCallback(async () => {
    if (!userCredentials) {
      setError('Veritabanı bağlantısı bulunamadı');
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const result = await getRelationshipNormalizationPreview(
        userCredentials.uri,
        userCredentials.userName,
        userCredentials.password,
        userCredentials.database
      );

      setPreview(result);

      // Grupları düzenlenebilir formata dönüştür
      const editableGroups: EditableGroup[] = result.groups
        .filter((g) => g.needs_change && g.original_types.length > 1)
        .map((g) => ({
          ...g,
          selected: true,
          editedName: g.suggested_name,
        }));

      setGroups(editableGroups);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Analiz başarısız');
    } finally {
      setLoading(false);
    }
  }, [userCredentials]);

  const handleApply = useCallback(async () => {
    if (!userCredentials) {
      setError('Veritabanı bağlantısı bulunamadı');
      return;
    }

    const selectedGroups = groups.filter((g) => g.selected);
    if (selectedGroups.length === 0) {
      setError('Lütfen en az bir grup seçin');
      return;
    }

    setApplying(true);
    setError(null);

    try {
      // Grupları API formatına dönüştür
      const apiGroups = selectedGroups.map((g) => ({
        suggested_name: g.editedName,
        original_types: g.original_types,
        count: g.count,
        needs_change: true,
      }));

      const result = await applyRelationshipNormalization(
        userCredentials.uri,
        userCredentials.userName,
        userCredentials.password,
        userCredentials.database,
        apiGroups
      );

      if (result.success) {
        setSuccess(`✅ ${result.total_changed} relationship başarıyla güncellendi`);
        setPreview(null);
        setGroups([]);
      } else {
        setError(`Bazı hatalar oluştu: ${result.errors.join(', ')}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Uygulama başarısız');
    } finally {
      setApplying(false);
    }
  }, [userCredentials, groups]);

  const handleToggleGroup = (index: number) => {
    setGroups((prev) =>
      prev.map((g, i) => (i === index ? { ...g, selected: !g.selected } : g))
    );
  };

  const handleEditName = (index: number, newName: string) => {
    setGroups((prev) =>
      prev.map((g, i) => (i === index ? { ...g, editedName: newName } : g))
    );
  };

  const handleSelectAll = (selected: boolean) => {
    setGroups((prev) => prev.map((g) => ({ ...g, selected })));
  };

  const selectedCount = groups.filter((g) => g.selected).length;
  const totalTypesToMerge = groups
    .filter((g) => g.selected)
    .reduce((sum, g) => sum + g.original_types.length, 0);

  return (
    <Flex flexDirection='column' gap='4'>
      <Typography variant='body-medium'>
        Bu araç, benzer anlama gelen ama farklı yazılmış relationship type'larını tespit eder ve 
        standartlaştırmanıza yardımcı olur. LLM kullanarak semantik benzerlik analizi yapar.
      </Typography>

      {error && (
        <Banner type='danger' closeable onClose={() => setError(null)}>
          {error}
        </Banner>
      )}

      {success && (
        <Banner type='success' closeable onClose={() => setSuccess(null)}>
          {success}
        </Banner>
      )}

      <Flex gap='2'>
        <Button
          onClick={handleAnalyze}
          loading={loading}
          disabled={loading || applying}
        >
          <ArrowPathIconOutline className='w-4 h-4 mr-2' />
          {loading ? 'Analiz Ediliyor...' : 'Analiz Et'}
        </Button>

        {groups.length > 0 && (
          <Button
            onClick={handleApply}
            loading={applying}
            disabled={applying || selectedCount === 0}
            color='primary'
          >
            <PlayIconSolid className='w-4 h-4 mr-2' />
            {applying ? 'Uygulanıyor...' : `Uygula (${selectedCount} grup, ${totalTypesToMerge} type)`}
          </Button>
        )}
      </Flex>

      {preview && groups.length === 0 && (
        <Banner type='info'>
          Normalizasyon gerekmiyor - tüm relationship type'ları zaten standart.
        </Banner>
      )}

      {preview && groups.length > 0 && (
        <>
          <Flex justifyContent='space-between' alignItems='center'>
            <Typography variant='subheading-medium'>
              📊 {preview.total_types} type analiz edildi, {groups.length} grup birleştirme önerisi
            </Typography>
            <Flex gap='2'>
              <Button size='small' fill='outlined' onClick={() => handleSelectAll(true)}>
                Tümünü Seç
              </Button>
              <Button size='small' fill='outlined' onClick={() => handleSelectAll(false)}>
                Seçimi Kaldır
              </Button>
            </Flex>
          </Flex>

          <div className='max-h-96 overflow-y-auto border rounded-lg'>
            {groups.map((group, index) => (
              <div
                key={index}
                className={`p-4 border-b last:border-b-0 ${
                  group.selected ? 'bg-blue-50' : 'bg-gray-50'
                }`}
              >
                <Flex justifyContent='space-between' alignItems='flex-start'>
                  <Flex alignItems='flex-start' gap='3'>
                    <Checkbox
                      isChecked={group.selected}
                      onChange={() => handleToggleGroup(index)}
                      ariaLabel={`Select ${group.suggested_name}`}
                    />
                    <div>
                      <Flex alignItems='center' gap='2' className='mb-2'>
                        <TextInput
                          value={group.editedName}
                          onChange={(e) => handleEditName(index, e.target.value)}
                          disabled={!group.selected}
                          className='font-mono text-sm'
                          style={{ width: '300px' }}
                        />
                        <StatusIndicator type='success'>
                          {group.count} relationship
                        </StatusIndicator>
                      </Flex>
                      <div className='text-sm text-gray-600'>
                        <span className='font-medium'>Birleşecek type'lar:</span>
                        <ul className='mt-1 ml-4 list-disc'>
                          {group.original_types.map((type, i) => (
                            <li
                              key={i}
                              className={`font-mono ${
                                type === group.editedName ? 'text-green-600 font-bold' : ''
                              }`}
                            >
                              {type}
                              {type === group.suggested_name && ' ✓'}
                            </li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  </Flex>
                </Flex>
              </div>
            ))}
          </div>
        </>
      )}

      {!preview && !loading && (
        <div className='text-center py-8 text-gray-500'>
          <Typography variant='body-medium'>
            Relationship type'larını analiz etmek için "Analiz Et" butonuna tıklayın.
          </Typography>
        </div>
      )}
    </Flex>
  );
}

