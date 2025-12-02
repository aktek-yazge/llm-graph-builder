import { CircleStackIcon, CpuChipIcon, ServerIcon } from '@heroicons/react/24/outline';
import { Banner, Dialog, Flex, ProgressBar, Typography } from '@neo4j-ndl/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ContainerStats, getSystemStats, SystemStatsResponse } from '../../../services/SystemStatsAPI';

// HDD icon component
const HddIcon = ({ className }: { className?: string }) => (
  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className={className}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 17.25v-.228a4.5 4.5 0 0 0-.12-1.03l-2.268-9.64a3.375 3.375 0 0 0-3.285-2.602H7.923a3.375 3.375 0 0 0-3.285 2.602l-2.268 9.64a4.5 4.5 0 0 0-.12 1.03v.228m19.5 0a3 3 0 0 1-3 3H5.25a3 3 0 0 1-3-3m19.5 0a3 3 0 0 0-3-3H5.25a3 3 0 0 0-3 3m16.5 0h.008v.008h-.008v-.008Zm-3 0h.008v.008h-.008v-.008Z" />
  </svg>
);

interface SystemMonitorModalProps {
  open: boolean;
  onClose: () => void;
}

// Parse memory usage string like "256.7MiB / 31.29GiB" to get used memory in MB
const parseMemUsage = (memUsage: string): number => {
  const match = memUsage.match(/^([\d.]+)(\w+)/);
  if (!match) return 0;
  const value = parseFloat(match[1]);
  const unit = match[2].toLowerCase();
  if (unit.includes('gib') || unit.includes('gb')) return value * 1024;
  if (unit.includes('mib') || unit.includes('mb')) return value;
  if (unit.includes('kib') || unit.includes('kb')) return value / 1024;
  return value;
};

// Format MB to human readable
const formatMB = (mb: number): string => {
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GiB`;
  return `${mb.toFixed(1)} MiB`;
};

// Container table with sorting and totals
const ContainerTable: React.FC<{ containers: ContainerStats[] }> = ({ containers }) => {
  // Sort containers by CPU percent (highest first)
  const sortedContainers = useMemo(() => {
    return [...containers].sort((a, b) => b.cpu_percent - a.cpu_percent);
  }, [containers]);

  // Calculate totals
  const totals = useMemo(() => {
    const totalCpu = containers.reduce((sum, c) => sum + c.cpu_percent, 0);
    const totalMemPercent = containers.reduce((sum, c) => sum + c.mem_percent, 0);
    const totalMemMB = containers.reduce((sum, c) => sum + parseMemUsage(c.mem_usage), 0);
    return {
      cpu: totalCpu,
      memPercent: totalMemPercent,
      memUsage: formatMB(totalMemMB),
    };
  }, [containers]);

  const getColorClass = (value: number, type: 'cpu' | 'mem') => {
    const threshold = type === 'cpu' ? { high: 80, medium: 50 } : { high: 80, medium: 50 };
    if (value > threshold.high) return 'text-red-500';
    if (value > threshold.medium) return 'text-yellow-500';
    return 'text-green-500';
  };

  return (
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
          {/* Totals row */}
          <tr className="bg-blue-50 dark:bg-blue-900/30 border-b-2 border-blue-200 dark:border-blue-700 font-semibold">
            <td className="py-2 px-2">
              <span className="text-blue-600 dark:text-blue-400">📊 TOPLAM</span>
            </td>
            <td className="text-right py-2 px-2">
              <span className={`font-mono ${getColorClass(totals.cpu, 'cpu')}`}>
                {totals.cpu.toFixed(1)}%
              </span>
            </td>
            <td className="text-right py-2 px-2 font-mono text-xs">
              {totals.memUsage}
            </td>
            <td className="text-right py-2 px-2">
              <span className={`font-mono ${getColorClass(totals.memPercent, 'mem')}`}>
                {totals.memPercent.toFixed(1)}%
              </span>
            </td>
            <td className="text-right py-2 px-2 hidden md:table-cell text-gray-400">-</td>
            <td className="text-right py-2 px-2 hidden lg:table-cell text-gray-400">-</td>
          </tr>
        </thead>
        <tbody>
          {sortedContainers.map((container: ContainerStats) => (
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
                <span className={`font-mono ${getColorClass(container.cpu_percent, 'cpu')}`}>
                  {container.cpu_percent.toFixed(1)}%
                </span>
              </td>
              <td className="text-right py-2 px-2 font-mono text-xs">
                {container.mem_usage}
              </td>
              <td className="text-right py-2 px-2">
                <span className={`font-mono ${getColorClass(container.mem_percent, 'mem')}`}>
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
  );
};

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

  const getProgressColorClass = (percent: number): string => {
    if (percent < 60) return 'progress-success';
    if (percent < 85) return 'progress-warning';
    return 'progress-danger';
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
              
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* CPU */}
                <div className="bg-white dark:bg-gray-700 rounded-lg p-3 shadow-sm">
                  <Flex justifyContent="space-between" alignItems="center" className="mb-2">
                    <Typography variant="body-medium">CPU Kullanımı</Typography>
                    <Typography variant="body-medium" className="font-bold">
                      {stats.data.host.cpu_percent.toFixed(1)}%
                    </Typography>
                  </Flex>
                  <div className={getProgressColorClass(stats.data.host.cpu_percent)}>
                    <ProgressBar size="small" value={stats.data.host.cpu_percent} />
                  </div>
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
                  <div className={getProgressColorClass(stats.data.host.memory.percent)}>
                    <ProgressBar size="small" value={stats.data.host.memory.percent} />
                  </div>
                  <Typography variant="body-small" className="text-gray-500 mt-1">
                    {stats.data.host.memory.used_gb.toFixed(1)} GB / {stats.data.host.memory.total_gb.toFixed(1)} GB
                    ({stats.data.host.memory.available_gb.toFixed(1)} GB kullanılabilir)
                  </Typography>
                </div>

                {/* Disk */}
                <div className="bg-white dark:bg-gray-700 rounded-lg p-3 shadow-sm">
                  <Flex justifyContent="space-between" alignItems="center" className="mb-2">
                    <Flex alignItems="center" gap="1">
                      <HddIcon className="w-4 h-4 text-purple-500" />
                      <Typography variant="body-medium">Disk Kullanımı</Typography>
                    </Flex>
                    <Typography variant="body-medium" className="font-bold">
                      {stats.data.host.disk?.percent.toFixed(1)}%
                    </Typography>
                  </Flex>
                  <div className={getProgressColorClass(stats.data.host.disk?.percent || 0)}>
                    <ProgressBar size="small" value={stats.data.host.disk?.percent || 0} />
                  </div>
                  <Typography variant="body-small" className="text-gray-500 mt-1">
                    {stats.data.host.disk?.used_gb.toFixed(1)} GB / {stats.data.host.disk?.total_gb.toFixed(1)} GB
                    ({stats.data.host.disk?.free_gb.toFixed(1)} GB boş)
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
                <ContainerTable containers={stats.data.containers} />
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

