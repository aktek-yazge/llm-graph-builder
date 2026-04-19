import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [tenantName, setTenantName] = useState('');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (password.length < 6) {
      setError('Sifre en az 6 karakter olmalidir.');
      return;
    }
    setLoading(true);
    try {
      await register({ tenant_name: tenantName, email, password, full_name: fullName });
      navigate('/dashboard', { replace: true });
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Kayit basarisiz. Bilgilerinizi kontrol edin.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-gray-900">Agent Builder</h1>
          <p className="text-sm text-gray-500 mt-1">Yeni hesap olusturun</p>
        </div>

        <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-sm border border-gray-200 p-6 space-y-4">
          {error && (
            <div className="p-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg">{error}</div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Sirket / Organizasyon</label>
            <input
              type="text" required value={tenantName} onChange={(e) => setTenantName(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Sirket adi"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Ad Soyad</label>
            <input
              type="text" required value={fullName} onChange={(e) => setFullName(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="Adiniz Soyadiniz"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input
              type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="ornek@sirket.com"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Sifre</label>
            <input
              type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="En az 6 karakter"
            />
          </div>

          <button
            type="submit" disabled={loading}
            className="w-full py-2.5 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
          >
            {loading ? 'Hesap olusturuluyor...' : 'Kayit Ol'}
          </button>

          <p className="text-center text-sm text-gray-500">
            Zaten hesabiniz var mi?{' '}
            <Link to="/login" className="text-blue-600 hover:underline font-medium">
              Giris Yap
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
