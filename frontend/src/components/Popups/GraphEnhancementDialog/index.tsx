import { tokens } from '@neo4j-ndl/base';
import { Dialog, Flex, Tabs, Typography, useMediaQuery } from '@neo4j-ndl/react';
import { Dispatch, SetStateAction, useState } from 'react';
import graphenhancement from '../../../assets/images/graph-enhancements.svg';
import { useFileContext } from '../../../context/UsersFiles';
import deleteOrphanAPI from '../../../services/DeleteOrphanNodes';
import { OptionType } from '../../../types';
import AdditionalInstructionsText from './AdditionalInstructions';
import DeduplicationTab from './Deduplication';
import DeletePopUpForOrphanNodes from './DeleteTabForOrphanNodes';
import NewEntityExtractionSetting from './EnitityExtraction/NewEntityExtractionSetting';
import PostProcessingCheckList from './PostProcessingCheckList';
import RelationshipNormalizationTab from './RelationshipNormalizationTab';

export default function GraphEnhancementDialog({
  open,
  onClose,
  combinedPatterns,
  setCombinedPatterns,
  combinedNodes,
  setCombinedNodes,
  combinedRels,
  setCombinedRels,
}: {
  open: boolean;
  onClose: () => void;
  combinedPatterns: string[];
  setCombinedPatterns: Dispatch<SetStateAction<string[]>>;
  combinedNodes: OptionType[];
  setCombinedNodes: Dispatch<SetStateAction<OptionType[]>>;
  combinedRels: OptionType[];
  setCombinedRels: Dispatch<SetStateAction<OptionType[]>>;
}) {
  const { breakpoints } = tokens;
  const [orphanDeleteAPIloading, setorphanDeleteAPIloading] = useState<boolean>(false);
  const {
    setShowTextFromSchemaDialog,
    setSchemaLoadDialog,
    setPredefinedSchemaDialog,
    setUserDefinedPattern,
    setUserDefinedNodes,
    setUserDefinedRels,
    setDbPattern,
    setDbNodes,
    setDbRels,
    setSchemaValNodes,
    setSchemaValRels,
    setSchemaTextPattern,
    setPreDefinedNodes,
    setPreDefinedRels,
    setPreDefinedPattern,
    setSelectedPreDefOption,
    allPatterns,
    setDataImporterSchemaDialog,
    setImporterNodes,
    setImporterPattern,
    setImporterRels,
  } = useFileContext();
  const isTablet = useMediaQuery(`(min-width:${breakpoints.xs}) and (max-width: ${breakpoints.lg})`);

  const orphanNodesDeleteHandler = async (selectedEntities: string[]) => {
    try {
      setorphanDeleteAPIloading(true);
      await deleteOrphanAPI(selectedEntities);
      setorphanDeleteAPIloading(false);
    } catch (error) {
      setorphanDeleteAPIloading(false);
      console.log(error);
    }
  };

  const handleOnclose = () => {
    if (allPatterns.length > 0) {
      onClose();
      return;
    }
    // User
    setUserDefinedPattern([]);
    setUserDefinedNodes([]);
    setUserDefinedRels([]);
    // DB
    setDbPattern([]);
    setDbNodes([]);
    setDbRels([]);
    // Text
    setSchemaTextPattern([]);
    setSchemaValNodes([]);
    setSchemaValRels([]);
    // Predefined
    setPreDefinedNodes([]);
    setPreDefinedRels([]);
    setPreDefinedPattern([]);
    // combined Nodes and rels
    setCombinedNodes([]);
    setCombinedRels([]);
    setCombinedPatterns([]);
    // Data Importer
    setImporterNodes([]);
    setImporterPattern([]);
    setImporterRels([]);
    setSelectedPreDefOption(null);
    onClose();
  };

  const [activeTab, setactiveTab] = useState<number>(0);
  return (
    <Dialog
      modalProps={{
        id: 'graph-enhancement-popup',
        className: 'n-p-token-4 n-rounded-lg',
      }}
      isOpen={open}
      size='unset'
      hasDisabledCloseButton={false}
      onClose={handleOnclose}
    >
      <Dialog.Header className='flex justify-between self-end mb-0! '>
        <div className='n-bg-palette-neutral-bg-weak px-4'>
          <div className='flex! flex-row items-center mb-2'>
            <img
              src={graphenhancement}
              style={{
                width: isTablet ? 170 : 220,
                height: isTablet ? 170 : 220,
                marginRight: 10,
                objectFit: 'contain',
              }}
              loading='lazy'
              alt='graph-enhancement-options-logo'
            />
            <div className='flex flex-col'>
              <Typography variant={isTablet ? 'h5' : 'h2'}>Graph Geliştirmeleri</Typography>
              <Typography variant={isTablet ? 'subheading-small' : 'subheading-medium'} className='mb-2'>
                {isTablet
                  ? `Bu araç seti Bilgi Graph'ınızın kalitesini artırmanıza yardımcı olacak`
                  : `Bu araç seti, olası tekrarlanan entity'leri ve bağlantısız nodeları kaldırarak ve entity 
                çıkarma sürecinin kalitesini artırmak için Graph Şeması belirleyerek Bilgi Graph'ınızın kalitesini 
                artırmanıza yardımcı olacak`}
              </Typography>
              <Flex className='pt-2'>
                <Tabs fill='underline' onChange={setactiveTab} size={isTablet ? 'small' : 'large'} value={activeTab}>
                  <Tabs.Tab
                    tabId={0}
                    htmlAttributes={{
                      'aria-label': 'Entity Çıkarma Ayarları',
                    }}
                  >
                    Entity Çıkarma Ayarları
                  </Tabs.Tab>
                  <Tabs.Tab
                    tabId={1}
                    htmlAttributes={{
                      'aria-label': 'Ek Talimatlar',
                    }}
                  >
                    Ek Talimatlar
                  </Tabs.Tab>
                  <Tabs.Tab
                    tabId={2}
                    htmlAttributes={{
                      'aria-label': 'Bağlantısız Nodelar',
                    }}
                  >
                    Bağlantısız Nodelar
                  </Tabs.Tab>
                  <Tabs.Tab
                    tabId={3}
                    htmlAttributes={{
                      'aria-label': 'Tekrarlanan Nodelar',
                    }}
                  >
                    Nodelarin Tekrarını Giderme
                  </Tabs.Tab>
                  <Tabs.Tab
                    tabId={4}
                    htmlAttributes={{
                      'aria-label': 'İşlem Sonrası Görevler',
                    }}
                  >
                    İşlem Sonrası Görevler
                  </Tabs.Tab>
                  <Tabs.Tab
                    tabId={5}
                    htmlAttributes={{
                      'aria-label': 'İlişki Normalizasyonu',
                    }}
                  >
                    İlişki Normalizasyonu
                  </Tabs.Tab>
                </Tabs>
              </Flex>
            </div>
          </div>
        </div>
      </Dialog.Header>
      <Dialog.Content className='flex flex-col n-gap-token- grow w-[90%] mx-auto'>
        <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4' value={activeTab} tabId={0}>
          <div className='w-[80%] mx-auto'>
            <NewEntityExtractionSetting
              view='Tabs'
              openTextSchema={() => {
                setShowTextFromSchemaDialog({ triggeredFrom: 'enhancementtab', show: true });
              }}
              openLoadSchema={() => setSchemaLoadDialog({ triggeredFrom: 'enhancementtab', show: true })}
              openPredefinedSchema={() => {
                setPredefinedSchemaDialog({ triggeredFrom: 'enhancementtab', show: true });
              }}
              closeEnhanceGraphSchemaDialog={onClose}
              settingView='headerView'
              combinedPatterns={combinedPatterns}
              setCombinedPatterns={setCombinedPatterns}
              combinedNodes={combinedNodes}
              setCombinedNodes={setCombinedNodes}
              combinedRels={combinedRels}
              setCombinedRels={setCombinedRels}
              openDataImporterSchema={() => {
                setDataImporterSchemaDialog({ triggeredFrom: 'enhancementtab', show: true });
              }}
            />
          </div>
        </Tabs.TabPanel>
        <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={1}>
          <AdditionalInstructionsText closeEnhanceGraphSchemaDialog={onClose} />
        </Tabs.TabPanel>
        <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={2}>
          <DeletePopUpForOrphanNodes deleteHandler={orphanNodesDeleteHandler} loading={orphanDeleteAPIloading} />
        </Tabs.TabPanel>
        <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={3}>
          <DeduplicationTab />
        </Tabs.TabPanel>
        <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={4}>
          <PostProcessingCheckList />
        </Tabs.TabPanel>
        <Tabs.TabPanel className='n-flex n-flex-col n-gap-token-4 n-p-token-6' value={activeTab} tabId={5}>
          <RelationshipNormalizationTab />
        </Tabs.TabPanel>
      </Dialog.Content>
    </Dialog>
  );
}
