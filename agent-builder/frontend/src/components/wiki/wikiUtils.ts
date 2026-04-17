import type { WikiCategory } from '../../services/evolvingApi';

export const CATEGORY_LABELS: Record<WikiCategory, string> = {
  entities: 'Entities',
  relationships: 'Relationships',
  patterns: 'Patterns',
  analysis: 'Analysis',
  sources: 'Sources',
  general: 'General',
};

export const CATEGORY_COLORS: Record<WikiCategory, string> = {
  entities: '#6366f1',
  relationships: '#ec4899',
  patterns: '#22c55e',
  analysis: '#f97316',
  sources: '#0ea5e9',
  general: '#64748b',
};

export const CATEGORY_ORDER: WikiCategory[] = [
  'entities',
  'relationships',
  'patterns',
  'analysis',
  'sources',
  'general',
];

export function pageName(path: string): string {
  return path.split('/').pop() || path;
}

export function categoryOf(path: string): WikiCategory {
  const parts = path.split('/');
  if (parts.length > 1 && (CATEGORY_ORDER as string[]).includes(parts[0])) {
    return parts[0] as WikiCategory;
  }
  return 'general';
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

const WIKILINK_RE = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;

export type WikilinkToken =
  | { type: 'text'; value: string }
  | { type: 'link'; target: string; label: string };

export function tokenizeWikilinks(content: string): WikilinkToken[] {
  const tokens: WikilinkToken[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  WIKILINK_RE.lastIndex = 0;
  while ((match = WIKILINK_RE.exec(content)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ type: 'text', value: content.slice(lastIndex, match.index) });
    }
    const target = match[1].trim();
    const label = (match[2] || match[1]).trim();
    tokens.push({ type: 'link', target, label });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < content.length) {
    tokens.push({ type: 'text', value: content.slice(lastIndex) });
  }
  return tokens;
}

/**
 * Markdown+wikilink sanitization layer: we expand [[Target]] to
 * an inline marker that react-markdown can render via a custom
 * component. We use a zero-width marker that won't appear in normal
 * markdown so it survives parsing intact.
 */
export function wikilinksToMarkdown(content: string): string {
  return content.replace(WIKILINK_RE, (_m, target: string, alias?: string) => {
    const label = (alias || target).trim();
    const path = encodeURIComponent(target.trim());
    return `[${label}](wikilink://${path})`;
  });
}
