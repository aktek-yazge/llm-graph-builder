import { useAuth0 } from '@auth0/auth0-react';
import { Button, Drawer, Flex, StatusIndicator, Typography, useMediaQuery } from '@neo4j-ndl/react';
import React, { Suspense, lazy, useMemo, useState } from 'react';
import { useAlertContext } from '../../context/Alert';
import { useCredentials } from '../../context/UserCredentials';
import { useFileContext } from '../../context/UsersFiles';
import { DrawerProps } from '../../types';
import { APP_SOURCES } from '../../utils/Constants';
import S3Component from '../DataSources/AWS/S3Bucket';
import GCSButton from '../DataSources/GCS/GCSButton';
import DropZone from '../DataSources/Local/DropZone';
import DropZoneV2 from '../DataSources/Local/DropZoneV2';
import CustomAlert from '../UI/Alert';
import FallBackDialog from '../UI/FallBackDialog';
import GenericButton from '../WebSources/GenericSourceButton';
import GenericModal from '../WebSources/GenericSourceModal';
const S3Modal = lazy(() => import('../DataSources/AWS/S3Modal'));
const GCSModal = lazy(() => import('../DataSources/GCS/GCSModal'));
const DrawerDropzone: React.FC<DrawerProps> = ({
  isExpanded,
  toggleS3Modal,
  toggleGCSModal,
  toggleGenericModal,
  shows3Modal,
  showGCSModal,
  showGenericModal,
}) => {
  const { closeAlert, alertState } = useAlertContext();
  const { isReadOnlyUser, isBackendConnected, connectionStatus } = useCredentials();
  const { loginWithRedirect } = useAuth0();
  const isLargeDesktop = useMediaQuery('(min-width:1440px)');
  const { filesData } = useFileContext();

  // V2 Upload System Toggle
  const [useV2Upload, setUseV2Upload] = useState(true); // Default to V2
  const isYoutubeOnly = useMemo(
    () => APP_SOURCES.includes('youtube') && !APP_SOURCES.includes('wiki') && !APP_SOURCES.includes('web'),
    []
  );
  const isWikipediaOnly = useMemo(
    () => APP_SOURCES.includes('wiki') && !APP_SOURCES.includes('youtube') && !APP_SOURCES.includes('web'),
    []
  );
  const isWebOnly = useMemo(
    () => APP_SOURCES.includes('web') && !APP_SOURCES.includes('youtube') && !APP_SOURCES.includes('wiki'),
    []
  );
  if (!isLargeDesktop) {
    return null;
  }
  return (
    <div className='flex relative min-h-[calc(-58px+100vh)]'>
      <Drawer
        isExpanded={isExpanded}
        position='left'
        type='push'
        isCloseable={false}
        htmlAttributes={{ style: { height: 'initial' } }}
      >
        {!isReadOnlyUser ? (
          <Drawer.Body className='overflow-hidden! w-[294px]!'>
            {alertState.showAlert && (
              <CustomAlert
                severity={alertState.alertType}
                open={alertState.showAlert}
                handleClose={closeAlert}
                alertMessage={alertState.alertMessage}
              />
            )}
            <div className='flex flex-col h-full'>
              <div className='relative h-full'>
                {process.env.VITE_ENV !== 'PROD' && (
                  <div className='mx-6 flex! items-center justify-between pb-6'>
                    <Typography variant='body-medium' className='flex items-center gap-1'>
                      <StatusIndicator type={isBackendConnected ? 'success' : 'danger'} />
                      <span>Backend connection status</span>
                    </Typography>
                  </div>
                )}
                {!connectionStatus && (
                  <div className='mx-6 flex items-center justify-between pb-6'>
                    <Typography variant='body-medium' className='flex items-center gap-1'>
                      <StatusIndicator type={connectionStatus ? 'success' : 'danger'} />
                      <span>Connect to Neo4j to upload documents</span>
                    </Typography>
                  </div>
                )}
                <div className={`${!connectionStatus ? 'cursor-not-allowed' : ''} h-full`}>
                  <div className={`resource-sections ${!connectionStatus ? 'blur-sm pointer-events-none' : ''}`}>
                    <Flex gap='6' className='h-full source-container'>
                      {APP_SOURCES.includes('local') && (
                        <div className='px-6 outline-dashed outline-2 outline-offset-2 outline-gray-100 mt-3 imageBg'>
                          {/* V1/V2 Upload Toggle */}
                          <div className='mb-3 flex items-center justify-between'>
                            <Typography variant='body-medium'>
                              Upload System: {useV2Upload ? 'Queue (V2)' : 'Direct (V1)'}
                            </Typography>
                            <Button size='small' onClick={() => setUseV2Upload(!useV2Upload)}>
                              Switch to {useV2Upload ? 'V1' : 'V2'}
                            </Button>
                          </div>

                          {/* Conditional DropZone Rendering */}
                          {useV2Upload ? <DropZoneV2 /> : <DropZone />}
                        </div>
                      )}
                      {APP_SOURCES.some((source) => ['youtube', 'wiki', 'web'].includes(source)) && (
                        <div className='outline-dashed imageBg w-[245px]'>
                          <GenericButton openModal={toggleGenericModal} />
                          <GenericModal
                            isOnlyYoutube={isYoutubeOnly}
                            isOnlyWikipedia={isWikipediaOnly}
                            isOnlyWeb={isWebOnly}
                            open={showGenericModal}
                            closeHandler={toggleGenericModal}
                          />
                        </div>
                      )}
                      {APP_SOURCES.includes('s3') && (
                        <div className='outline-dashed imageBg w-[245px]'>
                          <S3Component openModal={toggleS3Modal} />
                          <Suspense fallback={<FallBackDialog />}>
                            <S3Modal hideModal={toggleS3Modal} open={shows3Modal} />
                          </Suspense>
                        </div>
                      )}
                      {APP_SOURCES.includes('gcs') && (
                        <div className='outline-dashed imageBg w-[245px]'>
                          <GCSButton openModal={toggleGCSModal} />
                          <Suspense fallback={<FallBackDialog />}>
                            <GCSModal openGCSModal={toggleGCSModal} open={showGCSModal} hideModal={toggleGCSModal} />
                          </Suspense>
                        </div>
                      )}
                    </Flex>
                  </div>
                </div>
              </div>
            </div>
          </Drawer.Body>
        ) : (
          <Drawer.Body className='overflow-hidden! w-[294px]!'>
            <Typography
              variant='body-small'
              className='cursor-pointer'
              htmlAttributes={{
                onClick: () => {
                  loginWithRedirect();
                },
              }}
            >
              <StatusIndicator type={'danger'} />
              <span className='text-center mx-1'>
                {filesData.length === 0
                  ? `It seems like you haven't ingested any data yet Please log in to the main application.`
                  : 'You must be logged in to process this data. Please log in to the main application'}
              </span>
            </Typography>
            <div className={`h-full cursor-pointer`} onClick={() => loginWithRedirect()}>
              <div className={`resource-sections blur-sm pointer-events-none`}>
                <Flex gap='6' className='h-full source-container'>
                  {APP_SOURCES.includes('local') && (
                    <div className='px-6 outline-dashed outline-2 outline-offset-2 outline-gray-100 mt-3 imageBg'>
                      <DropZone />
                    </div>
                  )}
                  {APP_SOURCES.some((source) => ['youtube', 'wiki', 'web'].includes(source)) && (
                    <div className='outline-dashed imageBg w-[245px]'>
                      <GenericButton openModal={toggleGenericModal} />
                      <GenericModal
                        isOnlyYoutube={isYoutubeOnly}
                        isOnlyWikipedia={isWikipediaOnly}
                        isOnlyWeb={isWebOnly}
                        open={showGenericModal}
                        closeHandler={toggleGenericModal}
                      />
                    </div>
                  )}
                  {APP_SOURCES.includes('s3') && (
                    <div className='outline-dashed imageBg w-[245px]'>
                      <S3Component openModal={toggleS3Modal} />
                      <Suspense fallback={<FallBackDialog />}>
                        <S3Modal hideModal={toggleS3Modal} open={shows3Modal} />
                      </Suspense>
                    </div>
                  )}
                  {APP_SOURCES.includes('gcs') && (
                    <div className='outline-dashed imageBg w-[245px]'>
                      <GCSButton openModal={toggleGCSModal} />
                      <Suspense fallback={<FallBackDialog />}>
                        <GCSModal openGCSModal={toggleGCSModal} open={showGCSModal} hideModal={toggleGCSModal} />
                      </Suspense>
                    </div>
                  )}
                </Flex>
              </div>
            </div>
          </Drawer.Body>
        )}
      </Drawer>
    </div>
  );
};
export default DrawerDropzone;
