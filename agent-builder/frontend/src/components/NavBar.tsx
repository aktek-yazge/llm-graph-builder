import { Link, useLocation } from 'react-router-dom';
import {
  LayoutGrid,
  Bot,
  Workflow,
  BookOpen,
  FileText,
  FlaskConical,
  ShieldCheck,
  Wrench,
  Cpu,
  LogOut,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const NAV_ITEMS: { to: string; label: string; icon: LucideIcon }[] = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutGrid },
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/workflows', label: 'Workflows', icon: Workflow },
  { to: '/resources', label: 'Kaynaklar', icon: FileText },
  { to: '/knowledges', label: 'Knowledges', icon: BookOpen },
  { to: '/evaluations', label: 'Evaluations', icon: FlaskConical },
  { to: '/guardrails', label: 'Guardrails', icon: ShieldCheck },
  { to: '/tools', label: 'Tools', icon: Wrench },
  { to: '/models', label: 'Models', icon: Cpu },
];

export default function NavBar() {
  const location = useLocation();
  const { user, logout } = useAuth();

  return (
    <nav className="bg-white border-b border-gray-100 px-5 h-12 flex items-center justify-between shrink-0">
      <Link to="/dashboard" className="flex items-center gap-2.5 group">
        <span className="text-sm font-semibold text-gray-900 tracking-tight">
          Agent Builder
        </span>
      </Link>

      <div className="flex items-center gap-0.5">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => {
          const active = location.pathname === to || location.pathname.startsWith(to + '/');
          return (
            <Link
              key={to}
              to={to}
              className={`relative flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[12px] font-medium transition-colors ${
                active
                  ? 'text-gray-900'
                  : 'text-gray-400 hover:text-gray-600'
              }`}
            >
              <Icon size={14} strokeWidth={1.75} />
              <span className="hidden md:inline">{label}</span>
              {active && (
                <span className="absolute bottom-0 left-3 right-3 h-[2px] bg-gray-900 rounded-full" />
              )}
            </Link>
          );
        })}
      </div>

      {user && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500 hidden lg:inline">
            {user.full_name || user.email}
          </span>
          <button
            onClick={logout}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-colors"
            title="Cikis Yap"
          >
            <LogOut size={14} strokeWidth={1.75} />
          </button>
        </div>
      )}
    </nav>
  );
}
