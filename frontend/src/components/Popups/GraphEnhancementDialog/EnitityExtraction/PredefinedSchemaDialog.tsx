import { Dialog, Button, Select } from '@neo4j-ndl/react';
import { useState, useMemo, useEffect } from 'react';
import PatternContainer from '../../GraphEnhancementDialog/EnitityExtraction/PatternContainer';
import { OptionType, TupleType } from '../../../../types';
import { extractOptions, getSelectedTriplets, updateSourceTargetTypeOptions } from '../../../../utils/Utils';
import SchemaViz from '../../../../components/Graph/SchemaViz';
import { getDefaultSchemaExamples, appLabels } from '../../../../utils/Constants';
import { useFileContext } from '../../../../context/UsersFiles';
import { useCredentials } from '../../../../context/UserCredentials';
import QABasedExtractionModal from '../../../QABasedExtraction/QABasedExtraction';
import { listQASchemas, loadQASchema } from '../../../../API/Index';

interface SchemaFromTextProps {
  open: boolean;
  onClose: () => void;
  onApply: (
    patterns: string[],
    nodes: OptionType[],
    rels: OptionType[],
    updatedSource: OptionType[],
    updatedTarget: OptionType[],
    updatedType: OptionType[]
  ) => void;
}

const PredefinedSchemaDialog = ({ open, onClose, onApply }: SchemaFromTextProps) => {
  const defaultExamples = useMemo(() => getDefaultSchemaExamples(), []);
  const { userCredentials } = useCredentials();
  const {
    setPreDefinedPattern,
    preDefinedPattern,
    preDefinedNodes,
    setPreDefinedNodes,
    preDefinedRels,
    setPreDefinedRels,
    setSelectedPreDefOption,
    selectedPreDefOption,
    sourceOptions,
    setSourceOptions,
    targetOptions,
    setTargetOptions,
    typeOptions,
    setTypeOptions,
  } = useFileContext();

  const [openGraphView, setOpenGraphView] = useState<boolean>(false);
  const [viewPoint, setViewPoint] = useState<string>('');
  const [showQAModal, setShowQAModal] = useState<boolean>(false);
  const [qaSchemas, setQaSchemas] = useState<OptionType[]>([]);
  const [allSchemaOptions, setAllSchemaOptions] = useState<OptionType[]>([]);

  // QA şemalarını backend'den yükle
  const loadQASchemas = async () => {
    try {
      const response = await listQASchemas();
      if (response.data.status === 'Success' && response.data.data) {
        const qaSchemaOptions = response.data.data.map((schema: any) => ({
          label: `${schema.document_name} - ${schema.domain} (QA)`,
          value: `qa_${schema.filename}`,
          triplet: [], // Şu anlık boş, yüklenirken doldurulacak
          qaSchema: true,
          schemaInfo: schema,
        }));
        setQaSchemas(qaSchemaOptions);

        // Varsayılan + QA şemalarını birleştir
        const combined = [...defaultExamples, ...qaSchemaOptions];
        setAllSchemaOptions(combined);
      }
    } catch (error) {
      // QA şema yükleme hatası - sessizce devam et
      setAllSchemaOptions(defaultExamples);
    }
  };

  // Component mount olduğunda QA şemalarını yükle
  useEffect(() => {
    if (open) {
      loadQASchemas();
    }
  }, [open, defaultExamples]);

  const handleRemovePattern = (patternToRemove: string) => {
    const updatedPatterns = preDefinedPattern.filter((p) => p !== patternToRemove);
    if (updatedPatterns.length === 0) {
      setSelectedPreDefOption(null);
      setPreDefinedPattern([]);
      setPreDefinedNodes([]);
      setPreDefinedRels([]);
      return;
    }
    setPreDefinedPattern(updatedPatterns);
    const selectedTriplets: TupleType[] = getSelectedTriplets(
      selectedPreDefOption ? [selectedPreDefOption] : []
    ).filter((t) => updatedPatterns.includes(t.label));
    const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(selectedTriplets);
    setPreDefinedNodes(nodeLabelOptions);
    setPreDefinedRels(relationshipTypeOptions);
  };
  const onChangeSchema = async (selectedOption: OptionType | null | void) => {
    if (!selectedOption) {
      setSelectedPreDefOption(null);
      setPreDefinedPattern([]);
      setPreDefinedNodes([]);
      setPreDefinedRels([]);
      return;
    }

    setSelectedPreDefOption(selectedOption);

    // QA şeması mı kontrol et
    if ((selectedOption as any).qaSchema && (selectedOption as any).schemaInfo) {
      try {
        // QA şemasını backend'den yükle
        const response = await loadQASchema((selectedOption as any).schemaInfo.file_path);
        if (response.data.status === 'Success' && response.data.data?.new_schema_format?.triplet) {
          const triplets = response.data.data.new_schema_format.triplet;
          setPreDefinedPattern(triplets);

          // Triplet'lerden nodes ve relationships çıkar
          const selectedTriplets: TupleType[] = triplets.map((triplet: string) => ({
            label: triplet,
            value: triplet,
          }));

          const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(selectedTriplets);
          setPreDefinedNodes(nodeLabelOptions);
          setPreDefinedRels(relationshipTypeOptions);
          return;
        }
      } catch (error) {
        // QA şema yükleme hatası
        alert('QA şeması yüklenirken hata oluştu.');
        return;
      }
    }

    // Normal (varsayılan) şemalar için eski mantık
    const selectedTriplets: TupleType[] = getSelectedTriplets([selectedOption]);
    setPreDefinedPattern(selectedTriplets.map((t) => t.label));
    const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(selectedTriplets);
    setPreDefinedNodes(nodeLabelOptions);
    setPreDefinedRels(relationshipTypeOptions);
  };

  const handleSchemaView = () => {
    setOpenGraphView(true);
    setViewPoint('showSchemaView');
  };

  const handlePreDefinedSchemaApply = async () => {
    const [newSourceOptions, newTargetOptions, newTypeOptions] = await updateSourceTargetTypeOptions({
      patterns: preDefinedPattern.map((label) => ({ label, value: label })),
      currentSourceOptions: sourceOptions,
      currentTargetOptions: targetOptions,
      currentTypeOptions: typeOptions,
      setSourceOptions,
      setTargetOptions,
      setTypeOptions,
    });
    onApply(preDefinedPattern, preDefinedNodes, preDefinedRels, newSourceOptions, newTargetOptions, newTypeOptions);
    onClose();
  };

  const handleCancel = () => {
    setSelectedPreDefOption(null);
    setPreDefinedPattern([]);
    setPreDefinedNodes([]);
    setPreDefinedRels([]);
    onClose();
  };

  const handleQASuccess = (qaData: any) => {
    setShowQAModal(false);

    // QA extraction'dan dönen schema'yı predefined schema olarak ekle
    if (qaData?.new_schema_format?.triplet) {
      const triplets = qaData.new_schema_format.triplet;
      const schemaName = qaData.new_schema_format.schema || qaData.domain || 'qa_extracted';

      // Triplet'leri pattern container'a ekle
      setPreDefinedPattern(triplets);

      // Schema'yı seçili option olarak ayarla
      const qaSchemaOption = {
        label: `${schemaName} (QA Extracted)`,
        value: schemaName,
        triplet: triplets,
      };
      setSelectedPreDefOption(qaSchemaOption);

      // Triplet'lerden nodes ve relationships çıkar
      const selectedTriplets: TupleType[] = triplets.map((triplet: string) => ({
        label: triplet,
        value: triplet,
      }));

      const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(selectedTriplets);
      setPreDefinedNodes(nodeLabelOptions);
      setPreDefinedRels(relationshipTypeOptions);

      // QA şema listesini yeniden yükle
      loadQASchemas();
    }
  };

  return (
    <>
      <Dialog
        size='medium'
        isOpen={open}
        onClose={() => {
          handleCancel();
        }}
        htmlAttributes={{
          'aria-labelledby': 'form-dialog-title',
        }}
      >
        <Dialog.Header>Entity Graph Extraction Settings</Dialog.Header>
        <Dialog.Content className='n-flex n-flex-col n-gap-token-6 p-6'>
          <div className='text-center'>
            <h5 className='text-lg font-semibold'>{appLabels.predefinedSchema}</h5>
          </div>

          {/* QA Tabanlı Şema Çıkarma Butonu - Daha Görünür */}
          <div className='n-flex n-justify-center n-gap-token-4 mb-6 p-4 bg-blue-50 rounded-lg border border-blue-200'>
            <Button onClick={() => setShowQAModal(true)} size='large' color='primary'>
              🤖 QA Tabanlı Şema Çıkar
            </Button>
            <p className='text-sm text-gray-600 mt-2'>Belge metninden otomatik olarak şema çıkarmak için tıklayın</p>
          </div>

          <Select
            helpText='Schema Examples (includes QA extracted schemas)'
            label='Predefined Schema'
            size='medium'
            selectProps={{
              isClearable: true,
              options: allSchemaOptions.length > 0 ? allSchemaOptions : defaultExamples,
              onChange: onChangeSchema,
              value: selectedPreDefOption,
              menuPosition: 'fixed',
            }}
            type='select'
          />
          <PatternContainer
            pattern={preDefinedPattern}
            handleRemove={handleRemovePattern}
            handleSchemaView={handleSchemaView}
            nodes={preDefinedNodes}
            rels={preDefinedRels}
          />
          <Dialog.Actions className='n-flex n-justify-end n-gap-token-4 pt-4'>
            <Button onClick={handleCancel} isDisabled={preDefinedPattern.length === 0}>
              Cancel
            </Button>
            <Button onClick={handlePreDefinedSchemaApply} isDisabled={preDefinedPattern.length === 0}>
              Apply
            </Button>
          </Dialog.Actions>
        </Dialog.Content>
      </Dialog>
      {openGraphView && (
        <SchemaViz
          open={openGraphView}
          setGraphViewOpen={setOpenGraphView}
          viewPoint={viewPoint}
          nodeValues={preDefinedNodes ?? []}
          relationshipValues={preDefinedRels ?? []}
        />
      )}

      {/* QA Tabanlı Extraction Modal */}
      {userCredentials && (
        <QABasedExtractionModal
          open={showQAModal}
          onClose={() => setShowQAModal(false)}
          userCredentials={userCredentials}
          onSuccess={handleQASuccess}
        />
      )}
    </>
  );
};
export default PredefinedSchemaDialog;
