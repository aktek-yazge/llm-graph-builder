import { type FC } from 'react';
import { Flex, Button, Text } from '@chakra-ui/react';

interface Props {
  workflowName: string;
  workflowStatus: string;
  version: number;
  nodeCount: number;
  onValidate: () => void;
  onTestRun: () => void;
  onFullRun: () => void;
  onPublish: () => void;
  isRunning?: boolean;
}

const statusLabels: Record<string, string> = {
  draft: 'DRAFT',
  published: 'PUBLISHED',
  archived: 'ARCHIVED',
};

const Toolbar: FC<Props> = ({
  workflowName,
  workflowStatus,
  version,
  nodeCount,
  onValidate,
  onTestRun,
  onFullRun,
  onPublish,
  isRunning,
}) => {
  return (
    <Flex
      position="absolute"
      top={3}
      right={3}
      zIndex={10}
      bg="white"
      border="1px solid"
      borderColor="#e9ecef"
      borderRadius="10px"
      px={3}
      py={1.5}
      align="center"
      gap={3}
      shadow="0 4px 12px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.04)"
    >
      <Text fontSize="13px" fontWeight="600" color="#212529" noOfLines={1} maxW="120px">
        {workflowName}
      </Text>
      <Flex align="center" gap={1.5}>
        <Text fontSize="11px" color="#adb5bd" fontWeight="500">
          v{version}
        </Text>
        <Text fontSize="10px" color="#adb5bd" textTransform="uppercase" letterSpacing="0.03em">
          {statusLabels[workflowStatus] || workflowStatus}
        </Text>
        <Text fontSize="11px" color="#dee2e6">&middot;</Text>
        <Text fontSize="11px" color="#adb5bd">
          {nodeCount} node
        </Text>
      </Flex>
      <Flex gap={1}>
        <Button size="xs" variant="ghost" onClick={onValidate} isDisabled={isRunning} fontWeight="500" fontSize="12px">
          Dogrula
        </Button>
        <Button size="xs" variant="ghost" onClick={onTestRun} isDisabled={isRunning} fontWeight="500" fontSize="12px">
          Test
        </Button>
        <Button size="xs" variant="ghost" onClick={onFullRun} isDisabled={isRunning} fontWeight="500" fontSize="12px">
          Calistir
        </Button>
        <Button size="xs" onClick={onPublish} isDisabled={isRunning} fontWeight="500" fontSize="12px">
          Yayinla
        </Button>
      </Flex>
    </Flex>
  );
};

export default Toolbar;
