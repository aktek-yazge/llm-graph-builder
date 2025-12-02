import { useState, useEffect, useRef, useCallback } from 'react';
import { Dialog, Typography, Flex, ProgressBar, Banner } from '@neo4j-ndl/react';
import { ServerIcon, CpuChipIcon, CircleStackIcon } from '@heroicons/react/24/outline';
import { getSystemStats, SystemStatsResponse, ContainerStats } from '../../../services/SystemStatsAPI';

interface SystemMonitorModalProps {
  open: boolean;
  onClose: () => void;
}

const SystemMonitorModal: React.FC<SystemMonitorModalProps> = ({ open, onClose }) => {
  const [stats, setStats] = useState<SystemStatsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  const fetchStats = useCallback(async () => {
    try {
      setLoading(true);
      const response = await getSystemStats();
      setStats(response);
      setError(null);
    } catch (err) {
      setError('Sunucu istatistikleri alınamadı');
      console.error('System stats error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      // İlk yükleme
      fetchStats();
      
      // Her 1 saniyede bir güncelle
      intervalRef.current = setInterval(fetchStats, 1000);
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [open, fetchStats]);

  const getProgressColor = (percent: number): 'success' | 'warning' | 'danger' => {
    if (percent < 60) return 'success';
    if (percent < 85) return 'warning';
    return 'danger';
  };

  const formatBytes = (memUsage: string): string => {
    return memUsage;
  };

  return (
    <Dialog
      isOpen={open}
      onClose={onClose}
      size="large"
      hasDisabledCloseButton={false}
    >
      <Dialog.Header>
        <Flex alignItems="center" gap="2">
          <ServerIcon className="w-6 h-6 text-blue-500" />
          <Typography variant="h3">Sistem İzleme</Typography>
        </Flex>
      </Dialog.Header>
      
      <Dialog.Content className="flex flex-col gap-6 min-h-[400px]">
        {error && (
          <Banner type="danger" title="Hata">
            {error}
          </Banner>
        )}

        {stats?.data && (
          <>
            {/* Host İstatistikleri */}
            <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
              <Flex alignItems="center" gap="2" className="mb-4">
                <CpuChipIcon className="w-5 h-5 text-green-500" />
                <Typography variant="h5">Host Sistemi</Typography>
              </Flex>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* CPU */}
                <div className="bg-white dark:bg-gray-700 rounded-lg p-3 shadow-sm">
                  <Flex justifyContent="space-between" alignItems="center" className="mb-2">
                    <Typography variant="body-medium">CPU Kullanımı</Typography>
                    <Typography variant="body-medium" className="font-bold">
                      {stats.data.host.cpu_percent.toFixed(1)}%
                    </Typography>
                  </Flex>
                  <ProgressBar 
                    value={stats.data.host.cpu_percent} 
                    color={getProgressColor(stats.data.host.cpu_percent)}
                  />
                  <Typography variant="body-small" className="text-gray-500 mt-1">
                    {stats.data.host.cpu_count} CPU • Load: {stats.data.host.load_average['1min']}, {stats.data.host.load_average['5min']}, {stats.data.host.load_average['15min']}
                  </Typography>
                </div>

                {/* Memory */}
                <div className="bg-white dark:bg-gray-700 rounded-lg p-3 shadow-sm">
                  <Flex justifyContent="space-between" alignItems="center" className="mb-2">
                    <Typography variant="body-medium">RAM Kullanımı</Typography>
                    <Typography variant="body-medium" className="font-bold">
                      {stats.data.host.memory.percent.toFixed(1)}%
                    </Typography>
                  </Flex>
                  <ProgressBar 
                    value={stats.data.host.memory.percent} 
                    color={getProgressColor(stats.data.host.memory.percent)}
                  />
                  <Typography variant="body-small" className="text-gray-500 mt-1">
                    {stats.data.host.memory.used_gb.toFixed(1)} GB / {stats.data.host.memory.total_gb.toFixed(1)} GB
                    ({stats.data.host.memory.available_gb.toFixed(1)} GB kullanılabilir)
                  </Typography>
                </div>
              </div>
            </div>

            {/* Docker Konteynerler */}
            <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
              <Flex alignItems="center" gap="2" className="mb-4">
                <CircleStackIcon className="w-5 h-5 text-blue-500" />
                <Typography variant="h5">Docker Konteynerler</Typography>
                <span className="bg-blue-100 text-blue-800 text-xs font-medium px-2 py-0.5 rounded-full">
                  {stats.data.containers.length}
                </span>
              </Flex>

              {stats.data.containers.length === 0 ? (
                <Typography variant="body-medium" className="text-gray-500">
                  Çalışan konteyner bulunamadı
                </Typography>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b dark:border-gray-600">
                        <th className="text-left py-2 px-2">Konteyner</th>
                        <th className="text-right py-2 px-2">CPU %</th>
                        <th className="text-right py-2 px-2">RAM</th>
                        <th className="text-right py-2 px-2">RAM %</th>
                        <th className="text-right py-2 px-2 hidden md:table-cell">Net I/O</th>
                        <th className="text-right py-2 px-2 hidden lg:table-cell">Block I/O</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stats.data.containers.map((container: ContainerStats) => (
                        <tr 
                          key={container.container_id} 
                          className="border-b dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700"
                        >
                          <td className="py-2 px-2">
                            <div className="flex flex-col">
                              <span className="font-medium truncate max-w-[200px]" title={container.name}>
                                {container.name}
                              </span>
                              <span className="text-xs text-gray-400">{container.container_id}</span>
                            </div>
                          </td>
                          <td className="text-right py-2 px-2">
                            <span className={`font-mono ${
                              container.cpu_percent > 80 ? 'text-red-500' : 
                              container.cpu_percent > 50 ? 'text-yellow-500' : 'text-green-500'
                            }`}>
                              {container.cpu_percent.toFixed(1)}%
                            </span>
                          </td>
                          <td className="text-right py-2 px-2 font-mono text-xs">
                            {formatBytes(container.mem_usage)}
                          </td>
                          <td className="text-right py-2 px-2">
                            <span className={`font-mono ${
                              container.mem_percent > 80 ? 'text-red-500' : 
                              container.mem_percent > 50 ? 'text-yellow-500' : 'text-green-500'
                            }`}>
                              {container.mem_percent.toFixed(1)}%
                            </span>
                          </td>
                          <td className="text-right py-2 px-2 font-mono text-xs hidden md:table-cell">
                            {container.net_io}
                          </td>
                          <td className="text-right py-2 px-2 font-mono text-xs hidden lg:table-cell">
                            {container.block_io}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Son güncelleme */}
            <Flex justifyContent="space-between" alignItems="center" className="text-gray-400 text-xs">
              <span>Son güncelleme: {new Date(stats.data.timestamp).toLocaleTimeString('tr-TR')}</span>
              <span className={loading ? 'animate-pulse' : ''}>
                {loading ? '⟳ Güncelleniyor...' : '● Canlı (1sn)'}
              </span>
            </Flex>
          </>
        )}

        {!stats?.data && !error && (
          <Flex justifyContent="center" alignItems="center" className="h-64">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
          </Flex>
        )}
      </Dialog.Content>
    </Dialog>
  );
};

export default SystemMonitorModal;

