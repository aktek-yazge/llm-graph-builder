import { useState, useEffect, useRef } from 'react';
import { ResourceType } from '../services/resourceApi';

interface MetadataRow {
  key: string;
  value: string;
}

interface CreateResourceModalProps {
  open: boolean;
  onClose: () => void;
  onCreate: (data: {
    name: string;
    type: ResourceType;
    description: string;
    url?: string;
    notebook_id?: string;
    metadata: Record<string, any>;
    uploadMode?: 'file' | 'folder';
  }) => void;
  creating?: boolean;
}

const RESOURCE_TYPES: {
  type: ResourceType | 'folder';
  label: string;
  desc: string;
  icon: React.ReactNode;
}[] = [
  {
    type: 'minio',
    label: 'Dosya Yukle',
    desc: 'PDF, goruntu, metin dosyalari',
    icon: (
      <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    type: 'folder',
    label: 'Klasor Yukle',
    desc: 'Tum klasoru toplu yukle',
    icon: (
      <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
      </svg>
    ),
  },
  {
    type: 'link',
    label: 'Web Sitesi',
    desc: 'URL adresini kaynak olarak ekle',
    icon: (
      <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
      </svg>
    ),
  },
  {
    type: 'youtube',
    label: 'YouTube',
    desc: 'YouTube video linki',
    icon: (
      <svg className="w-7 h-7" fill="currentColor" viewBox="0 0 24 24">
        <path d="M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
      </svg>
    ),
  },
  {
    type: 'image',
    label: 'Goruntu',
    desc: 'Tek gorsel dosyasi',
    icon: (
      <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    type: 'notebooklm',
    label: 'NotebookLM',
    desc: 'Google NotebookLM entegrasyonu',
    icon: (
      <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
      </svg>
    ),
  },
];

export default function CreateResourceModal({ open, onClose, onCreate, creating }: CreateResourceModalProps) {
  const [step, setStep] = useState<'type' | 'form'>('type');
  const [selectedType, setSelectedType] = useState<ResourceType | 'folder' | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [url, setUrl] = useState('');
  const [notebookId, setNotebookId] = useState('');
  const [metadataRows, setMetadataRows] = useState<MetadataRow[]>([]);
  const [showMetadata, setShowMetadata] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setStep('type');
      setSelectedType(null);
      setName('');
      setDescription('');
      setUrl('');
      setNotebookId('');
      setMetadataRows([]);
      setShowMetadata(false);
    }
  }, [open]);

  useEffect(() => {
    if (step === 'form' && nameRef.current) {
      nameRef.current.focus();
    }
  }, [step]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  function selectType(t: ResourceType | 'folder') {
    setSelectedType(t);
    setStep('form');
  }

  function addMetadataRow() {
    setMetadataRows((prev) => [...prev, { key: '', value: '' }]);
  }

  function updateMetadataRow(idx: number, field: 'key' | 'value', val: string) {
    setMetadataRows((prev) => prev.map((r, i) => (i === idx ? { ...r, [field]: val } : r)));
  }

  function removeMetadataRow(idx: number) {
    setMetadataRows((prev) => prev.filter((_, i) => i !== idx));
  }

  function buildMetadata(): Record<string, any> {
    const m: Record<string, any> = {};
    for (const row of metadataRows) {
      if (row.key.trim()) m[row.key.trim()] = row.value;
    }
    return m;
  }

  function handleSubmit() {
    if (!name.trim() || !selectedType) return;
    const actualType: ResourceType = selectedType === 'folder' ? 'minio' : selectedType;
    const uploadMode = selectedType === 'minio' ? 'file' : selectedType === 'folder' ? 'folder' : undefined;

    onCreate({
      name: name.trim(),
      type: actualType,
      description: description.trim(),
      url: url.trim() || undefined,
      notebook_id: notebookId.trim() || undefined,
      metadata: buildMetadata(),
      uploadMode: uploadMode as 'file' | 'folder' | undefined,
    });
  }

  const needsUrl = selectedType === 'link' || selectedType === 'youtube';
  const needsNotebook = selectedType === 'notebooklm';
  const needsUpload = selectedType === 'minio' || selectedType === 'folder' || selectedType === 'image';
  const canSubmit = name.trim() && (!needsUrl || url.trim()) && (!needsNotebook || notebookId.trim());

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <div className="flex items-center gap-3">
            {step === 'form' && (
              <button
                onClick={() => { setStep('type'); setSelectedType(null); }}
                className="p-1 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
            )}
            <h2 className="text-lg font-semibold text-gray-900">
              {step === 'type' ? 'Yeni Resource' : `${RESOURCE_TYPES.find((t) => t.type === selectedType)?.label || 'Resource'}`}
            </h2>
          </div>
          <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Step 1: Type Selection */}
        {step === 'type' && (
          <div className="p-6">
            <p className="text-sm text-gray-500 mb-5">Kaynak turunu secin:</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {RESOURCE_TYPES.map((rt) => (
                <button
                  key={rt.type}
                  onClick={() => selectType(rt.type as ResourceType | 'folder')}
                  className="flex flex-col items-center gap-2 p-4 rounded-xl border-2 border-gray-100 hover:border-blue-400 hover:bg-blue-50/50 transition-all text-center group"
                >
                  <span className="text-gray-400 group-hover:text-blue-500 transition-colors">{rt.icon}</span>
                  <span className="text-sm font-medium text-gray-700 group-hover:text-blue-700">{rt.label}</span>
                  <span className="text-[11px] text-gray-400 leading-tight">{rt.desc}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 2: Type-Specific Form */}
        {step === 'form' && selectedType && (
          <div className="p-6 space-y-4">
            {/* Name */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Isim *</label>
              <input
                ref={nameRef}
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Resource adi"
                className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            {/* Description */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Aciklama</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Bu resource ne icin kullanilacak?"
                rows={2}
                className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none"
              />
            </div>

            {/* URL field for link/youtube */}
            {needsUrl && (
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  {selectedType === 'youtube' ? 'YouTube URL *' : 'Web Sitesi URL *'}
                </label>
                <input
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder={selectedType === 'youtube' ? 'https://www.youtube.com/watch?v=...' : 'https://example.com'}
                  className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
                {selectedType === 'youtube' && url && (() => {
                  const match = url.match(/(?:v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/);
                  return match ? (
                    <p className="text-xs text-green-600 mt-1">Video ID: {match[1]}</p>
                  ) : url.length > 10 ? (
                    <p className="text-xs text-amber-600 mt-1">Gecerli bir YouTube URL'si girin</p>
                  ) : null;
                })()}
              </div>
            )}

            {/* NotebookLM field */}
            {needsNotebook && (
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Notebook ID *</label>
                <input
                  type="text"
                  value={notebookId}
                  onChange={(e) => setNotebookId(e.target.value)}
                  placeholder="NotebookLM notebook UUID"
                  className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
                <p className="text-xs text-gray-400 mt-1">
                  Agent bu notebook'a soru sorabilir ve deep research yapabilir.
                </p>
              </div>
            )}

            {/* Upload hint */}
            {needsUpload && (
              <div className="bg-blue-50 rounded-lg p-3 flex items-start gap-2">
                <svg className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p className="text-xs text-blue-700">
                  {selectedType === 'folder'
                    ? 'Resource olusturulduktan sonra klasor secici acilacak.'
                    : selectedType === 'image'
                    ? 'Resource olusturulduktan sonra gorsel secici acilacak.'
                    : 'Resource olusturulduktan sonra dosya secici acilacak.'}
                </p>
              </div>
            )}

            {/* Metadata Key-Value Editor */}
            <div>
              <button
                type="button"
                onClick={() => { setShowMetadata(!showMetadata); if (!showMetadata && metadataRows.length === 0) addMetadataRow(); }}
                className="flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-700 transition-colors"
              >
                <svg className={`w-3.5 h-3.5 transition-transform ${showMetadata ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                Metadata (opsiyonel)
              </button>

              {showMetadata && (
                <div className="mt-2 space-y-2">
                  {metadataRows.map((row, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <input
                        type="text"
                        value={row.key}
                        onChange={(e) => updateMetadataRow(idx, 'key', e.target.value)}
                        placeholder="Anahtar"
                        className="flex-1 px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-blue-400"
                      />
                      <input
                        type="text"
                        value={row.value}
                        onChange={(e) => updateMetadataRow(idx, 'value', e.target.value)}
                        placeholder="Deger"
                        className="flex-1 px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-blue-400"
                      />
                      <button
                        onClick={() => removeMetadataRow(idx)}
                        className="p-1 text-gray-300 hover:text-red-500 transition-colors"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    </div>
                  ))}
                  <button
                    onClick={addMetadataRow}
                    className="text-xs text-blue-600 hover:text-blue-700 flex items-center gap-1"
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    Satir ekle
                  </button>
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="flex items-center justify-end gap-2 pt-2 border-t border-gray-100">
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
              >
                Iptal
              </button>
              <button
                onClick={handleSubmit}
                disabled={!canSubmit || creating}
                className="px-5 py-2 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
              >
                {creating ? 'Olusturuluyor...' : 'Olustur'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
