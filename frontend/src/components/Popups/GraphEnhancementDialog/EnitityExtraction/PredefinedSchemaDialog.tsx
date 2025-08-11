import { Dialog, Button, Select } from '@neo4j-ndl/react';
import { useState, useMemo, useEffect, useCallback } from 'react';
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
  const [allSchemaOptions, setAllSchemaOptions] = useState<OptionType[]>([]);
  const [debugInfo, setDebugInfo] = useState<string>('');
  const [selectedSchemaTripletsCount, setSelectedSchemaTripletsCount] = useState<number>(0);

  // QA şemalarını backend'den yükle
  const loadQASchemas = useCallback(async () => {
    try {
      const response = await listQASchemas();
      if (response.data.status === 'Success' && response.data.data) {
        const qaSchemaOptions = response.data.data.map((schema: any) => ({
          label: `${schema.document_name} - ${schema.domain} (${schema.entities_count || 0} Nodes & ${schema.unique_relationship_types || schema.relationships_count || 0} Rel Types) (QA)`,
          value: `qa_${schema.filename}`,
          triplet: [], // Şu anlık boş, yüklenirken doldurulacak
          qaSchema: true,
          schemaInfo: schema,
        }));

        // Varsayılan + QA şemalarını birleştir
        const combined = [...defaultExamples, ...qaSchemaOptions];
        setAllSchemaOptions(combined);
        setDebugInfo(`QA şemaları yüklendi: ${qaSchemaOptions.length} schema, toplam: ${combined.length}`);
      } else {
        // QA şema yükleme hatası - sadece varsayılanları göster
        setAllSchemaOptions(defaultExamples);
        setDebugInfo('QA şema yükleme başarısız, sadece varsayılanlar yüklendi');
      }
    } catch (error) {
      // QA şema yükleme hatası - sadece varsayılanları göster
      setAllSchemaOptions(defaultExamples);
      setDebugInfo(`QA şema yükleme hatası: ${error}`);
    }
  }, [defaultExamples]);

  // Component mount olduğunda QA şemalarını yükle
  useEffect(() => {
    if (open) {
      loadQASchemas();
    }
  }, [open, loadQASchemas]);

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
      setSelectedSchemaTripletsCount(0); // Reset triplet count
      return;
    }

    setSelectedPreDefOption(selectedOption);

    // QA şeması mı kontrol et
    if ((selectedOption as any).qaSchema && (selectedOption as any).schemaInfo) {
      try {
        // QA şemasını backend'den yükle
        const response = await loadQASchema((selectedOption as any).schemaInfo.file_path);
        console.log(response.data);
        if (response.data.status === 'Success' && response.data.data) {
          const qaData = response.data.data;

          // schema varsa onu kullan (labels ve relationshipTypes ayrı ayrı)
          // if (qaData.schema) {
          //   const { labels, relationshipTypes } = qaData.schema;
          //   const triplets = qaData.triplets || [];

          //   setPreDefinedPattern(triplets);
          //   setSelectedSchemaTripletsCount(triplets.length); // Triplet sayısını set et

          //   // Labels ve relationships'i direkt al
          //   const nodeLabelOptions = labels.map((label: string) => ({
          //     label,
          //     value: label,
          //   }));

          //   const relationshipTypeOptions = relationshipTypes.map((relType: string) => ({
          //     label: relType,
          //     value: relType,
          //   }));

          //   setPreDefinedNodes(nodeLabelOptions);
          //   setPreDefinedRels(relationshipTypeOptions);
          //   return;
          // }
          // // Fallback: sadece tripletler varsa eski mantığı kullan
          // else if (qaData.triplets) {
          //   const { triplets } = qaData;
          //   setPreDefinedPattern(triplets);

          //   // Triplet'lerden nodes ve relationships çıkar
          //   const selectedTriplets: TupleType[] = triplets.map((triplet: string) => ({
          //     label: triplet,
          //     value: triplet,
          //   }));

          //   const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(selectedTriplets);
          //   setPreDefinedNodes(nodeLabelOptions);
          //   setPreDefinedRels(relationshipTypeOptions);
          //   return;
          // }

          if (qaData.triplets) {
            const { triplets } = qaData;

            // QA triplets için varsayılan şema mantığını kullan
            const selectedTriplets: TupleType[] = getSelectedTriplets([
              {
                label: 'QA Schema',
                value: JSON.stringify(triplets), // getSelectedTriplets JSON.parse ile array bekliyor
              },
            ]);

            setPreDefinedPattern(selectedTriplets.map((t) => t.label));
            setSelectedSchemaTripletsCount(selectedTriplets.length); // QA schema için de triplet sayısını set et
            const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(selectedTriplets);
            setPreDefinedNodes(nodeLabelOptions);
            setPreDefinedRels(relationshipTypeOptions);
            return;
          }
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
    setSelectedSchemaTripletsCount(selectedTriplets.length); // Default schema için de triplet sayısını set et
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
    setDebugInfo('');
    onClose();
  };

  const handleQASuccess = (qaData: any) => {
    setShowQAModal(false);
    setDebugInfo(`QA Success çağrıldı: ${qaData ? 'Data var' : 'Data yok'}`);

    // QA extraction'dan dönen schema'yı predefined schema olarak ekle
    // Yeni data yapısını kontrol et: qaData.triplets ve qaData.schema
    if (qaData?.triplets && qaData?.schema) {
      const { triplets, schema, domain } = qaData;
      const schemaName = schema.schema || domain || 'qa_extracted';
      const { labels, relationshipTypes } = schema;

      // Triplet'leri pattern container'a ekle
      setPreDefinedPattern(triplets);

      // Schema'yı seçili option olarak ayarla
      const qaSchemaOption = {
        label: `${schemaName} (QA Extracted)`,
        value: schemaName,
        triplet: triplets,
      };
      setSelectedPreDefOption(qaSchemaOption);

      // Labels ve relationships'i direkt kullan (schema'dan)
      const nodeLabelOptions = labels.map((label: string) => ({
        label,
        value: label,
      }));

      const relationshipTypeOptions = relationshipTypes.map((relType: string) => ({
        label: relType,
        value: relType,
      }));

      setPreDefinedNodes(nodeLabelOptions);
      setPreDefinedRels(relationshipTypeOptions);

      // QA şema listesini yeniden yükle
      setDebugInfo(
        `QA Success: ${triplets.length} triplets, ${labels.length} nodes, ${relationshipTypes.length} relation types - loadQASchemas çağrılacak...`
      );
      loadQASchemas();
    } else {
      setDebugInfo(
        `QA Success: Beklenen data yapısı bulunamadı. Triplets: ${Boolean(qaData?.triplets)}, schema: ${Boolean(qaData?.schema)}`
      );
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

          {/* Debug Info */}
          {debugInfo && <div className='p-2 bg-gray-100 rounded text-xs text-gray-600'>Debug: {debugInfo}</div>}

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
            tripletsCount={selectedSchemaTripletsCount}
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
