import { RichMessagePart } from '../services/agentBuilderApi';
import InlineUpload from './InlineUpload';

interface RichMessageProps {
  parts: RichMessagePart[];
  onAction?: (action: string) => void;
  onSuggestion?: (text: string) => void;
  onUploadFiles?: (files: File[]) => void;
  onTriggerSideUpload?: () => void;
}

export default function RichMessage({
  parts,
  onAction,
  onSuggestion,
  onUploadFiles,
  onTriggerSideUpload,
}: RichMessageProps) {
  if (!parts || parts.length === 0) return null;

  return (
    <div className="space-y-3">
      {parts.map((part, i) => (
        <RichPart
          key={i}
          part={part}
          onAction={onAction}
          onSuggestion={onSuggestion}
          onUploadFiles={onUploadFiles}
          onTriggerSideUpload={onTriggerSideUpload}
        />
      ))}
    </div>
  );
}

function RichPart({
  part,
  onAction,
  onSuggestion,
  onUploadFiles,
  onTriggerSideUpload,
}: {
  part: RichMessagePart;
  onAction?: (action: string) => void;
  onSuggestion?: (text: string) => void;
  onUploadFiles?: (files: File[]) => void;
  onTriggerSideUpload?: () => void;
}) {
  switch (part.type) {
    case 'text':
      return <p className="text-sm leading-relaxed whitespace-pre-wrap">{part.content}</p>;

    case 'upload_zone':
      return <InlineUpload onFiles={onUploadFiles} />;

    case 'trigger_side_upload':
      return (
        <button
          onClick={onTriggerSideUpload}
          className="w-full py-3 px-4 bg-indigo-50 border-2 border-dashed border-indigo-300 rounded-xl text-sm font-medium text-indigo-700 hover:bg-indigo-100 transition-colors"
        >
          Toplu Yukleme Panelini Ac
        </button>
      );

    case 'action_buttons': {
      const buttons = (part.metadata?.buttons as string[]) || [];
      return (
        <div className="flex flex-wrap gap-2">
          {buttons.map((btn) => {
            const variant = btn.toLowerCase().includes('onayla') || btn.toLowerCase().includes('evet')
              ? 'bg-green-600 hover:bg-green-700 text-white'
              : btn.toLowerCase().includes('reddet') || btn.toLowerCase().includes('hayir')
                ? 'bg-red-500 hover:bg-red-600 text-white'
                : 'bg-gray-100 hover:bg-gray-200 text-gray-700';
            return (
              <button
                key={btn}
                onClick={() => onAction?.(btn)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${variant}`}
              >
                {btn}
              </button>
            );
          })}
        </div>
      );
    }

    case 'card': {
      const title = (part.metadata?.title as string) || '';
      const body = (part.metadata?.body as string) || part.content;
      return (
        <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm">
          {title && <h4 className="font-semibold text-sm text-gray-900 mb-2">{title}</h4>}
          <div className="text-sm text-gray-700 whitespace-pre-wrap">{body}</div>
        </div>
      );
    }

    case 'suggestion': {
      const suggestions = (part.metadata?.suggestions as string[]) || [];
      return (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((s) => (
            <button
              key={s}
              onClick={() => onSuggestion?.(s)}
              className="px-3 py-1.5 bg-blue-50 text-blue-700 rounded-full text-xs font-medium hover:bg-blue-100 transition-colors border border-blue-200"
            >
              {s}
            </button>
          ))}
        </div>
      );
    }

    case 'progress': {
      const pct = (part.metadata?.percent as number) || 0;
      const label = (part.metadata?.label as string) || '';
      return (
        <div>
          <div className="flex justify-between text-xs text-gray-600 mb-1">
            <span>{label}</span>
            <span>%{pct}</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div className="bg-blue-600 h-2 rounded-full transition-all" style={{ width: `${Math.min(pct, 100)}%` }} />
          </div>
        </div>
      );
    }

    case 'status': {
      const status = (part.metadata?.status as string) || '';
      const label = (part.metadata?.label as string) || '';
      const colors: Record<string, string> = {
        success: 'bg-green-100 text-green-800 border-green-200',
        error: 'bg-red-100 text-red-800 border-red-200',
        warning: 'bg-yellow-100 text-yellow-800 border-yellow-200',
        info: 'bg-blue-100 text-blue-800 border-blue-200',
      };
      const cls = colors[status] || colors.info;
      return (
        <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm ${cls}`}>
          <span className="font-medium">{status}</span>
          {label && <span>{label}</span>}
        </div>
      );
    }

    case 'table':
      return (
        <div className="text-sm text-gray-700 whitespace-pre-wrap bg-gray-50 rounded-lg p-3 border border-gray-200">
          {part.content}
        </div>
      );

    default:
      return <p className="text-sm text-gray-700 whitespace-pre-wrap">{part.content}</p>;
  }
}
