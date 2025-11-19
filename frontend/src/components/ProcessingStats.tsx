import { Dialog, Flex, StatusIndicator, Typography } from '@neo4j-ndl/react';
import React, { useMemo } from 'react';
import { CustomFile } from '../types';

interface ProcessingStatsProps {
  files: CustomFile[];
  open: boolean;
  onClose: () => void;
}

const ProcessingStats: React.FC<ProcessingStatsProps> = ({ files, open, onClose }) => {
  const stats = useMemo(() => {
    const v2Files = files.filter((f) => f.fileSource === 'V2 Queue');
    
    return {
      chunking: {
        queued: v2Files.filter((f) => f.chunking_status === 'ready').length,
        processing: v2Files.filter((f) => f.chunking_status === 'chunking').length,
        completed: v2Files.filter((f) => f.chunking_status === 'chunked').length,
      },
      graph: {
        queued: v2Files.filter((f) => f.chunking_status === 'chunked' && f.graph_status === 'pending').length,
        processing: v2Files.filter((f) => f.graph_status === 'processing').length,
        completed: v2Files.filter((f) => f.graph_status === 'completed').length,
      },
      embedding: {
        queued: v2Files.filter((f) => f.chunking_status === 'chunked' && f.embedding_status === 'pending').length,
        processing: v2Files.filter((f) => f.embedding_status === 'processing').length,
        completed: v2Files.filter((f) => f.embedding_status === 'completed').length,
      },
      failed: v2Files.filter((f) => f.chunking_status === 'failed' || f.graph_status === 'failed' || f.embedding_status === 'failed').length,
      total: v2Files.length,
    };
  }, [files]);

  if (stats.total === 0) return null;

  return (
    <Dialog isOpen={open} onClose={onClose} size="large" aria-labelledby="stats-dialog-title">
      <Dialog.Header>Processing Statistics</Dialog.Header>
      <Dialog.Content>
        <div className="w-full p-4">
          <Flex flexDirection="row" gap="6" justifyContent="space-between" alignItems="center">
            
            {/* Chunking Stats */}
            <Flex flexDirection="column" gap="2" className="flex-1">
              <Typography variant="subheading-small" className="font-semibold text-gray-500 uppercase tracking-wider">
                Chunking Pipeline
              </Typography>
              <Flex gap="4">
                <div className="flex flex-col">
                  <span className="text-2xl font-bold">{stats.chunking.processing}</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <StatusIndicator type="info" /> Processing
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-2xl font-bold">{stats.chunking.queued}</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <StatusIndicator type="warning" /> Queued
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-2xl font-bold">{stats.chunking.completed}</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <StatusIndicator type="success" /> Chunked
                  </span>
                </div>
              </Flex>
            </Flex>

            {/* Divider */}
            <div className="w-px h-16 bg-gray-200 dark:bg-gray-700"></div>

            {/* Graph Stats */}
            <Flex flexDirection="column" gap="2" className="flex-1">
              <Typography variant="subheading-small" className="font-semibold text-gray-500 uppercase tracking-wider">
                Graph Pipeline
              </Typography>
              <Flex gap="4">
                <div className="flex flex-col">
                  <span className="text-2xl font-bold">{stats.graph.processing}</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <StatusIndicator type="info" /> Processing
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-2xl font-bold">{stats.graph.queued}</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <StatusIndicator type="warning" /> Queued
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-2xl font-bold">{stats.graph.completed}</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <StatusIndicator type="success" /> Completed
                  </span>
                </div>
              </Flex>
            </Flex>

            {/* Divider */}
            <div className="w-px h-16 bg-gray-200 dark:bg-gray-700"></div>

            {/* Embedding Stats */}
            <Flex flexDirection="column" gap="2" className="flex-1">
              <Typography variant="subheading-small" className="font-semibold text-gray-500 uppercase tracking-wider">
                Embedding Pipeline
              </Typography>
              <Flex gap="4">
                <div className="flex flex-col">
                  <span className="text-2xl font-bold">{stats.embedding.processing}</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <StatusIndicator type="info" /> Processing
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-2xl font-bold">{stats.embedding.queued}</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <StatusIndicator type="warning" /> Queued
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-2xl font-bold">{stats.embedding.completed}</span>
                  <span className="text-xs text-gray-500 flex items-center gap-1">
                    <StatusIndicator type="success" /> Completed
                  </span>
                </div>
              </Flex>
            </Flex>

            {/* Divider */}
            <div className="w-px h-16 bg-gray-200 dark:bg-gray-700"></div>

            {/* Failed Stats */}
            <Flex flexDirection="column" gap="2" className="min-w-[100px]">
              <Typography variant="subheading-small" className="font-semibold text-gray-500 uppercase tracking-wider">
                Issues
              </Typography>
              <div className="flex flex-col">
                <span className="text-2xl font-bold text-red-500">{stats.failed}</span>
                <span className="text-xs text-gray-500 flex items-center gap-1">
                  <StatusIndicator type="danger" /> Failed
                </span>
              </div>
            </Flex>

          </Flex>
        </div>
      </Dialog.Content>
    </Dialog>
  );
};

export default ProcessingStats;
