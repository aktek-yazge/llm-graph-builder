import { useRef, useState, useCallback } from 'react';

const ACCEPTED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.tif', '.txt', '.md'];

function isAcceptedFile(name: string): boolean {
  const ext = name.slice(name.lastIndexOf('.')).toLowerCase();
  return ACCEPTED_EXTENSIONS.includes(ext);
}

async function readEntryRecursive(entry: FileSystemEntry): Promise<File[]> {
  if (entry.isFile) {
    return new Promise((resolve) => {
      (entry as FileSystemFileEntry).file((f) => {
        resolve(isAcceptedFile(f.name) ? [f] : []);
      }, () => resolve([]));
    });
  }
  if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    const entries = await new Promise<FileSystemEntry[]>((resolve) => {
      const allEntries: FileSystemEntry[] = [];
      function readBatch() {
        reader.readEntries((batch) => {
          if (batch.length === 0) { resolve(allEntries); return; }
          allEntries.push(...batch);
          readBatch();
        }, () => resolve(allEntries));
      }
      readBatch();
    });
    const nested = await Promise.all(entries.map(readEntryRecursive));
    return nested.flat();
  }
  return [];
}

interface InlineUploadProps {
  onFiles?: (files: File[]) => void;
  accept?: string;
  maxFiles?: number;
  label?: string;
}

export default function InlineUpload({
  onFiles,
  accept = '.pdf,.png,.jpg,.jpeg,.tiff,.txt,.md',
  maxFiles = 50,
  label = 'Dosya veya klasor surukleyin, ya da tiklayin',
}: InlineUploadProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploaded, setUploaded] = useState(false);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);

      const items = e.dataTransfer.items;
      if (items?.length) {
        const entries = Array.from(items)
          .map((item) => item.webkitGetAsEntry?.())
          .filter(Boolean) as FileSystemEntry[];

        if (entries.length > 0) {
          const allFiles = (await Promise.all(entries.map(readEntryRecursive))).flat();
          const trimmed = allFiles.slice(0, maxFiles);
          if (trimmed.length > 0) {
            setSelectedFiles(trimmed);
            return;
          }
        }
      }

      const files = Array.from(e.dataTransfer.files)
        .filter((f) => isAcceptedFile(f.name))
        .slice(0, maxFiles);
      if (files.length > 0) {
        setSelectedFiles(files);
      }
    },
    [maxFiles],
  );

  function handleSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const raw = Array.from(e.target.files || []);
    const files = raw.filter((f) => isAcceptedFile(f.name)).slice(0, maxFiles);
    if (files.length > 0) {
      setSelectedFiles(files);
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
    if (folderInputRef.current) folderInputRef.current.value = '';
  }

  function handleUpload() {
    if (selectedFiles.length > 0 && onFiles) {
      onFiles(selectedFiles);
      setUploaded(true);
    }
  }

  function handleRemove(index: number) {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index));
  }

  if (uploaded) {
    return (
      <div className="bg-green-50 border border-green-200 rounded-xl p-4 text-center">
        <p className="text-sm text-green-700 font-medium">{selectedFiles.length} dosya yuklendi</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-xl p-6 text-center transition-colors ${
          dragOver ? 'border-blue-400 bg-blue-50' : 'border-gray-300 hover:border-blue-300 hover:bg-gray-50'
        }`}
      >
        <div className="text-gray-400 mb-1">
          <svg className="w-8 h-8 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
        </div>
        <p className="text-sm font-medium text-gray-600">{label}</p>
        <p className="text-xs text-gray-400 mt-1">PDF, goruntu veya metin dosyalari (maks {maxFiles} dosya)</p>
        <div className="flex justify-center gap-3 mt-3">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="px-3 py-1.5 text-xs text-blue-600 bg-blue-50 border border-blue-200 rounded-lg hover:bg-blue-100 flex items-center gap-1.5"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
            </svg>
            Dosya Sec
          </button>
          <button
            type="button"
            onClick={() => folderInputRef.current?.click()}
            className="px-3 py-1.5 text-xs text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100 flex items-center gap-1.5"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
            </svg>
            Klasor Sec
          </button>
        </div>
        <input ref={fileInputRef} type="file" multiple accept={accept} onChange={handleSelect} className="hidden" />
        <input
          ref={folderInputRef}
          type="file"
          /* @ts-expect-error webkitdirectory is non-standard but widely supported */
          webkitdirectory=""
          directory=""
          multiple
          onChange={handleSelect}
          className="hidden"
        />
      </div>

      {selectedFiles.length > 0 && (
        <>
          <div className="flex flex-wrap gap-2">
            {selectedFiles.map((f, i) => (
              <span key={i} className="flex items-center gap-1 px-2 py-1 bg-gray-100 rounded text-xs text-gray-700">
                {f.name}
                <button onClick={() => handleRemove(i)} className="text-gray-400 hover:text-red-500 ml-1">&times;</button>
              </span>
            ))}
          </div>
          <button
            onClick={handleUpload}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            {selectedFiles.length} Dosya Yukle
          </button>
        </>
      )}
    </div>
  );
}
