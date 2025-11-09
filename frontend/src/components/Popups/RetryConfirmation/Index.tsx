import { Banner, Dialog, Flex, Radio } from '@neo4j-ndl/react';
import { memo } from 'react';
import { useFileContext } from '../../../context/UsersFiles';
import { BannerAlertProps } from '../../../types';
import { RETRY_OPIONS } from '../../../utils/Constants';
import { capitalize } from '../../../utils/Utils';
import ButtonWithToolTip from '../../UI/ButtonWithToolTip';

function RetryConfirmationDialog({
  open,
  onClose,
  fileId,
  retryLoading,
  retryHandler,
  alertStatus,
  onBannerClose,
}: {
  open: boolean;
  onClose: () => void;
  fileId: string;
  retryLoading: boolean;
  alertStatus: BannerAlertProps;
  retryHandler: (filename: string, retryoption: string) => void;
  onBannerClose: () => void;
}) {
  const { filesData, setFilesData } = useFileContext();
  const file = filesData.find((c) => c.id === fileId);
  const RetryOptionsForFile = file?.status != 'Completed' ? RETRY_OPIONS : RETRY_OPIONS.slice(0, 2);
  return (
    <Dialog isOpen={open} onClose={onClose}>
      <Dialog.Header>Reprocess Options</Dialog.Header>
      <Dialog.Description>
        Clicking "Continue" will reset these files to "Chunked" status. This will allow graph creation to run again
        automatically.
      </Dialog.Description>
      <Dialog.Content>
        {alertStatus.showAlert && (
          <Banner isCloseable onClose={onBannerClose} className='my-4' type={alertStatus.alertType} usage='inline'>
            {alertStatus.alertMessage}
          </Banner>
        )}
        <Flex>
          {RetryOptionsForFile.map((o, i) => {
            return (
              <Radio
                key={`${o}${i}`}
                onChange={(e) => {
                  setFilesData((prev) => {
                    return prev.map((f) => {
                      return f.id === fileId ? { ...f, retryOptionStatus: e.target.checked, retryOption: o } : f;
                    });
                  });
                }}
                htmlAttributes={{
                  name: 'retryoptions',
                  onKeyDown: (e) => {
                    if (e.code === 'Enter' && file?.retryOption.length) {
                      retryHandler(file?.name as string, file?.retryOption as string);
                    }
                  },
                }}
                isChecked={o === file?.retryOption && file?.retryOptionStatus}
                label={o
                  .split('_')
                  .map((s) => capitalize(s))
                  .join(' ')}
              />
            );
          })}
        </Flex>
        <Dialog.Actions>
          <Dialog.Actions className='mt-3!'>
            <ButtonWithToolTip
              placement='left'
              label='Retry action button'
              text={!file?.retryOption?.length ? `Please Select One Of The Option` : 'Reset Status to Chunked'}
              loading={retryLoading}
              disabled={!file?.retryOption?.length}
              onClick={() => {
                retryHandler(file?.name as string, file?.retryOption as string);
              }}
            >
              Continue
            </ButtonWithToolTip>
          </Dialog.Actions>
        </Dialog.Actions>
      </Dialog.Content>
    </Dialog>
  );
}
export default memo(RetryConfirmationDialog);
