import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// see https://stackoverflow.com/questions/73834404/react-uncaught-referenceerror-process-is-not-defined
// otherwise use import.meta.env.VITE_BACKEND_API_URL and expose it as such with the VITE_ prefix
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  
  // Base path: VITE_BASE_PATH'ten al, yoksa DEPLOYMENT_ENV'e göre belirle
  // Preview/staging ortamlarında /wat/ prefix'i kullanılır
  const deploymentEnv = process.env.DEPLOYMENT_ENV || 'local';
  const basePath = env.VITE_BASE_PATH || (deploymentEnv === 'preview' ? '/wat/' : '/');
  
  return {
    base: basePath,
    define: {
      'process.env': env,
    },
    plugins: [react()],
    optimizeDeps: { esbuildOptions: { target: 'es2020' } },
    server: {
      host: true,
      // Traefik reverse proxy için izin verilen hostlar
      allowedHosts: ['localhost', 'wat.local', 'aksa.local', 'kormas.local', 'demoserver.yazge.aktekbilisim.com'],
    },
  };
});
