import { Flex, Typography, Button, IconButton, Select, Checkbox } from '@neo4j-ndl/react';
import { TrashIconOutline, PlusIconOutline } from '@neo4j-ndl/react/icons';
import { useFileContext } from '../../context/UsersFiles';
import { useMemo } from 'react';

// Entity relationship rule interface
interface EntityRelationshipRule {
  id: string;
  sourceNodeType: string;
  targetNodeType: string;
  relationshipType: string;
  removeExistingRelationships: boolean;
}

interface EntityRelationshipPostProcessingProps {
  isEnabled: boolean;
}

export default function EntityRelationshipPostProcessing({ isEnabled }: EntityRelationshipPostProcessingProps) {
  const { entityRelationshipRules, setEntityRelationshipRules, selectedNodes, selectedRels } = useFileContext();

  // localStorage'tan node ve relationship'leri al
  const commonNodeTypes = useMemo(() => {
    return selectedNodes.map((node) => ({
      label: node.label,
      value: node.value,
    }));
  }, [selectedNodes]);

  const commonRelationshipTypes = useMemo(() => {
    return selectedRels.map((rel) => {
      // source,relationship,target formatından sadece relationship kısmını al
      const parts = rel.value.split(',');
      const relationshipType = parts.length >= 2 ? parts[1] : rel.value;
      return {
        label: relationshipType,
        value: relationshipType,
      };
    });
  }, [selectedRels]);

  // Add new rule
  const addRule = () => {
    const newRule: EntityRelationshipRule = {
      id: Date.now().toString(),
      sourceNodeType: commonNodeTypes.length > 0 ? commonNodeTypes[0].value : '',
      targetNodeType: commonNodeTypes.length > 0 ? commonNodeTypes[0].value : '',
      relationshipType: commonRelationshipTypes.length > 0 ? commonRelationshipTypes[0].value : '',
      removeExistingRelationships: false,
    };
    setEntityRelationshipRules([...entityRelationshipRules, newRule]);
  };

  // Remove rule
  const removeRule = (ruleId: string) => {
    setEntityRelationshipRules(entityRelationshipRules.filter((rule: EntityRelationshipRule) => rule.id !== ruleId));
  };

  // Update rule
  const updateRule = (ruleId: string, updates: Partial<EntityRelationshipRule>) => {
    setEntityRelationshipRules(
      entityRelationshipRules.map((rule: EntityRelationshipRule) => {
        return rule.id === ruleId ? { ...rule, ...updates } : rule;
      })
    );
  };

  if (!isEnabled) {
    return null;
  }

  return (
    <div className='ml-6 mt-3 p-4 bg-gray-50 rounded-md border'>
      <Flex flexDirection='column' gap='4'>
        <Flex justifyContent='space-between' alignItems='center'>
          <Typography variant='body-medium' className='font-semibold'>
            Entity İlişki Kuralları
          </Typography>
          <Button size='small' fill='outlined' onClick={addRule}>
            <PlusIconOutline className='w-4 h-4 mr-1' />
            Kural Ekle
          </Button>
        </Flex>

        {entityRelationshipRules.length === 0 && (
          <div className='text-center py-4 text-gray-500'>
            <Typography variant='body-small'>
              Henüz kural eklenmemiş. Yukarıdaki "Kural Ekle" butonu ile yeni kural ekleyebilirsiniz.
            </Typography>
          </div>
        )}

        {entityRelationshipRules.map((rule: EntityRelationshipRule) => (
          <div key={rule.id} className='p-3 bg-white rounded border'>
            <Flex flexDirection='column' gap='3'>
              <Flex justifyContent='space-between' alignItems='center'>
                <Typography variant='body-small' className='font-medium'>
                  Kural #{rule.id.slice(-4)}
                </Typography>
                <IconButton
                  size='small'
                  isClean
                  onClick={() => removeRule(rule.id)}
                  ariaLabel={`Remove rule ${rule.id}`}
                >
                  <TrashIconOutline />
                </IconButton>
              </Flex>

              <div className='grid grid-cols-1 md:grid-cols-3 gap-3'>
                <div>
                  <label className='block text-xs font-medium text-gray-700 mb-1'>Kaynak Node Tipi</label>
                  <Select
                    type='creatable'
                    selectProps={{
                      value: { label: rule.sourceNodeType, value: rule.sourceNodeType },
                      onChange: (option: any) => updateRule(rule.id, { sourceNodeType: option.value }),
                      options: commonNodeTypes,
                      placeholder: 'Kaynak node tipi',
                      isSearchable: true,
                      isClearable: false,
                    }}
                    size='small'
                  />
                </div>

                <div>
                  <label className='block text-xs font-medium text-gray-700 mb-1'>Hedef Node Tipi</label>
                  <Select
                    type='creatable'
                    selectProps={{
                      value: { label: rule.targetNodeType, value: rule.targetNodeType },
                      onChange: (option: any) => updateRule(rule.id, { targetNodeType: option.value }),
                      options: commonNodeTypes,
                      placeholder: 'Hedef node tipi',
                      isSearchable: true,
                      isClearable: false,
                    }}
                    size='small'
                  />
                </div>

                <div>
                  <label className='block text-xs font-medium text-gray-700 mb-1'>İlişki Tipi</label>
                  <Select
                    type='creatable'
                    selectProps={{
                      value: { label: rule.relationshipType, value: rule.relationshipType },
                      onChange: (option: any) => updateRule(rule.id, { relationshipType: option.value }),
                      options: commonRelationshipTypes,
                      placeholder: 'İlişki tipi',
                      isSearchable: true,
                      isClearable: false,
                    }}
                    size='small'
                  />
                </div>
              </div>

              <div>
                <Checkbox
                  isChecked={rule.removeExistingRelationships}
                  onChange={(e) => updateRule(rule.id, { removeExistingRelationships: e.target.checked })}
                  label={
                    <Typography variant='body-small' className='text-gray-700'>
                      Mevcut ilişkileri kaldır ve yenilerini ekle
                    </Typography>
                  }
                  ariaLabel={`remove-existing-relationships-${rule.id}`}
                />
              </div>
            </Flex>
          </div>
        ))}
      </Flex>
    </div>
  );
}
