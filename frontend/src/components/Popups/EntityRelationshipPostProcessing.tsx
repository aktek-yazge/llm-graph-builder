import { Flex, Typography, Button, IconButton, Select, Checkbox } from '@neo4j-ndl/react';
import { TrashIconOutline, PlusIconOutline } from '@neo4j-ndl/react/icons';
import { useFileContext } from '../../context/UsersFiles';

// Entity relationship rule interface
interface EntityRelationshipRule {
  id: string;
  sourceNodeType: string;
  targetNodeType: string;
  relationshipType: string;
  removeExistingRelationships: boolean;
}

// Common node and relationship types
const commonNodeTypes = [
  { label: 'DocumentYear', value: 'DocumentYear' },
  { label: 'PublishYear', value: 'PublishYear' },
  { label: 'PolicyStartYear', value: 'PolicyStartYear' },
  { label: 'PolicyEndYear', value: 'PolicyEndYear' },
  { label: 'BirthYear', value: 'BirthYear' },
  { label: 'RegistrationYear', value: 'RegistrationYear' },
  { label: 'VehicleModelYear', value: 'VehicleModelYear' },
  { label: 'FirstRegistrationYear', value: 'FirstRegistrationYear' },
  { label: 'Date', value: 'Date' },
  { label: 'Person', value: 'Person' },
  { label: 'Company', value: 'Company' },
  { label: 'Location', value: 'Location' },
  { label: 'HomeAddress', value: 'HomeAddress' },
  { label: 'MailingAddress', value: 'MailingAddress' },
  { label: 'WorkAddress', value: 'WorkAddress' },
  { label: 'BillingAddress', value: 'BillingAddress' },
  { label: 'PropertyAddress', value: 'PropertyAddress' },
  { label: 'BuildingAddress', value: 'BuildingAddress' },
  { label: 'BusinessAddress', value: 'BusinessAddress' },
  { label: 'WarehouseAddress', value: 'WarehouseAddress' },
  { label: 'VehicleRegistrationAddress', value: 'VehicleRegistrationAddress' },
  { label: 'GarageAddress', value: 'GarageAddress' },
  { label: 'AccidentAddress', value: 'AccidentAddress' },
  { label: 'CompanyAddress', value: 'CompanyAddress' },
  { label: 'BranchAddress', value: 'BranchAddress' },
  { label: 'ClaimAddress', value: 'ClaimAddress' },
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

const commonRelationshipTypes = [
  { label: 'DOCUMENT_YEAR', value: 'DOCUMENT_YEAR' },
  { label: 'PUBLISHED_IN', value: 'PUBLISHED_IN' },
  { label: 'POLICY_STARTS_IN', value: 'POLICY_STARTS_IN' },
  { label: 'POLICY_ENDS_IN', value: 'POLICY_ENDS_IN' },
  { label: 'BORN_IN', value: 'BORN_IN' },
  { label: 'REGISTERED_IN', value: 'REGISTERED_IN' },
  { label: 'MODEL_YEAR', value: 'MODEL_YEAR' },
  { label: 'FIRST_REGISTERED_IN', value: 'FIRST_REGISTERED_IN' },
  { label: 'OCCURS_IN', value: 'OCCURS_IN' },
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

interface EntityRelationshipPostProcessingProps {
  isEnabled: boolean;
}

export default function EntityRelationshipPostProcessing({ isEnabled }: EntityRelationshipPostProcessingProps) {
  const { entityRelationshipRules, setEntityRelationshipRules } = useFileContext();

  // Add new rule
  const addRule = () => {
    const newRule: EntityRelationshipRule = {
      id: Date.now().toString(),
      sourceNodeType: 'DocumentYear',
      targetNodeType: 'Document',
      relationshipType: 'DOCUMENT_YEAR',
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
