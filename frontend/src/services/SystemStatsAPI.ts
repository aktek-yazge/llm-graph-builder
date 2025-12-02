import api from '../API/Index';

export interface ContainerStats {
  container_id: string;
  name: string;
  cpu_percent: number;
  mem_usage: string;
  mem_percent: number;
  net_io: string;
  block_io: string;
}

export interface HostStats {
  cpu_percent: number;
  cpu_count: number;
  load_average: {
    '1min': number;
    '5min': number;
    '15min': number;
  };
  memory: {
    total_gb: number;
    used_gb: number;
    available_gb: number;
    percent: number;
  };
}

export interface SystemStatsResponse {
  status: string;
  data?: {
    host: HostStats;
    containers: ContainerStats[];
    timestamp: string;
  };
  error?: string;
}

export const getSystemStats = async (): Promise<SystemStatsResponse> => {
  try {
    const response = await api.get('/system/stats');
    return response.data;
  } catch (error) {
    console.error('Error fetching system stats:', error);
    throw error;
  }
};

