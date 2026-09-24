import React, { memo, useMemo, useState } from 'react';

export interface DiffRow {
  kind: 'add' | 'del' | 'ctx' | 'hunk';
  content: string;
}

const TOKEN_RE = new RegExp(
  [
    '("(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\'|`(?:[^`\\\\]|\\\\.)*`)',
    '(\\/\\/[^\\n]*|#[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/)',
    '\\b(\\d+(?:\\.\\d+)?)\\b',
    '\\b(' + [
      'const', 'let', 'var', 'function', 'return', 'if', 'else', 'elif', 'for', 'while', 'break', 'continue',
      'class', 'def', 'import', 'from', 'as', 'try', 'except', 'finally', 'with', 'lambda', 'yield', 'pass',
      'None', 'True', 'False', 'and', 'or', 'not', 'in', 'is', 'switch', 'case', 'default', 'new', 'delete',
      'typeof', 'instanceof', 'void', 'do', 'throw', 'catch', 'async', 'await', 'static', 'public', 'private',
      'protected', 'readonly', 'extends', 'implements', 'interface', 'type', 'enum', 'namespace', 'export',
      'fun', 'val', 'string', 'int', 'float', 'bool', 'char', 'self', 'this', 'null', 'undefined', 'echo',
    ].join('|') + ')\\b',
    '\\b([A-Za-z_]\\w*)(?=\\s*\\()',
  ].join('|'),
  'g'
);

const SPAN_CACHE = new Map<string, React.ReactNode[]>();
const MAX_SPAN_CACHE = 2000;

function highlight(code: string): React.ReactNode[] {
  if (!code) return [];
  const cached = SPAN_CACHE.get(code);
  if (cached) return cached;

  const nodes: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  TOKEN_RE.lastIndex = 0;
  let key = 0;
  while ((m = TOKEN_RE.exec(code)) !== null) {
    if (m.index > last) nodes.push(code.slice(last, m.index));
    if (m[1]) nodes.push(<span key={key++} className="tok-s">{m[1]}</span>);
    else if (m[2]) nodes.push(<span key={key++} className="tok-c">{m[2]}</span>);
    else if (m[3]) nodes.push(<span key={key++} className="tok-n">{m[3]}</span>);
    else if (m[4]) nodes.push(<span key={key++} className="tok-k">{m[4]}</span>);
    else if (m[5]) nodes.push(<span key={key++} className="tok-f">{m[5]}</span>);
    last = m.index + m[0].length;
  }
  if (last < code.length) nodes.push(code.slice(last));

  if (SPAN_CACHE.size >= MAX_SPAN_CACHE) {
    SPAN_CACHE.delete(SPAN_CACHE.keys().next().value as string);
  }
  SPAN_CACHE.set(code, nodes);
  return nodes;
}

export function parseUnifiedDiff(diff: string): { rows: DiffRow[]; adds: number; dels: number } {
  const rows: DiffRow[] = [];
  let adds = 0;
  let dels = 0;
  for (const raw of diff.split('\n')) {
    if (!raw) continue;
    if (raw.startsWith('+++') || raw.startsWith('---')) continue;
    if (raw.startsWith('@@')) {
      rows.push({ kind: 'hunk', content: raw });
      continue;
    }
    if (raw.startsWith('+')) {
      rows.push({ kind: 'add', content: raw.slice(1) });
      adds++;
    } else if (raw.startsWith('-')) {
      rows.push({ kind: 'del', content: raw.slice(1) });
      dels++;
    } else {
      rows.push({ kind: 'ctx', content: raw.startsWith(' ') ? raw.slice(1) : raw });
    }
  }
  return { rows, adds, dels };
}

const FOLD_ROWS = 10;
const PREVIEW_ROWS = 5;

export const EditDiffCard: React.FC<{ diff: string; filePath: string }> = memo(({ diff, filePath }) => {
  const [expanded, setExpanded] = useState(false);
  const { rows, adds, dels } = useMemo(() => parseUnifiedDiff(diff), [diff]);

  const folded = rows.length > FOLD_ROWS && !expanded;
  const visible = folded ? rows.slice(0, PREVIEW_ROWS) : rows;

  return (
    <div className="tool-diff-card">
      <div className="tool-diff-header">
        <span className="tool-diff-title">{filePath.split(/[\\/]/).pop() || filePath}</span>
        {adds > 0 && <span className="tool-diff-stat add">+{adds}</span>}
        {dels > 0 && <span className="tool-diff-stat del">−{dels}</span>}
      </div>
      <div className="tool-diff-body">
        {visible.map((row, idx) => (
          <div key={idx} className={`tool-diff-row ${row.kind}`}>
            {row.kind === 'hunk'
              ? <span className="tool-diff-hunk">{row.content}</span>
              : (
                <>
                  <span className="tool-diff-sign">{row.kind === 'add' ? '+' : row.kind === 'del' ? '−' : ' '}</span>
                  <span className="tool-diff-code">{highlight(row.content)}</span>
                </>
              )}
          </div>
        ))}
      </div>
      {folded && (
        <button className="tool-diff-expand-btn" onClick={() => setExpanded(true)}>
          展开其余 {rows.length - PREVIEW_ROWS} 行
        </button>
      )}
      {rows.length > FOLD_ROWS && expanded && (
        <button className="tool-diff-expand-btn" onClick={() => setExpanded(false)}>
          收起
        </button>
      )}
    </div>
  );
});

EditDiffCard.displayName = 'EditDiffCard';

const MAX_OUTPUT_LINES = 10;
const PREVIEW_OUTPUT_LINES = 4;

export const ShellOutputCard: React.FC<{ result: any }> = memo(({ result }) => {
  const [expanded, setExpanded] = useState(false);
  const stdout = typeof result?.output === 'string' ? result.output : '';
  const stderr = typeof result?.stderr === 'string' ? result.stderr : '';
  const exitCode: number | null = typeof result?.exit_code === 'number' ? result.exit_code : null;
  const lines = useMemo(() => stdout.split('\n'), [stdout]);
  const folded = lines.length > MAX_OUTPUT_LINES && !expanded;
  const visible = folded ? lines.slice(0, PREVIEW_OUTPUT_LINES) : lines;

  return (
    <div className="tool-shell-card">
      <div className="tool-shell-header">
        <span className={`tool-shell-exit ${exitCode === 0 || exitCode === null ? 'ok' : 'fail'}`}>
          {exitCode === null ? 'exit' : `exit ${exitCode}`}
        </span>
        {result?.truncated && <span className="tool-shell-flag">输出被截断</span>}
        {stdout && <span className="tool-shell-flag">{lines.length} 行</span>}
      </div>
      {stdout && (
        <div className="tool-shell-body">
          {visible.map((line, idx) => (
            <div key={idx} className="tool-shell-line">{line || ' '}</div>
          ))}
        </div>
      )}
      {folded && (
        <button className="tool-diff-expand-btn" onClick={() => setExpanded(true)}>
          展开其余 {lines.length - PREVIEW_OUTPUT_LINES} 行
        </button>
      )}
      {expanded && lines.length > MAX_OUTPUT_LINES && (
        <button className="tool-diff-expand-btn" onClick={() => setExpanded(false)}>
          收起
        </button>
      )}
      {stderr && (
        <pre className="tool-shell-stderr">{stderr.slice(0, expanded ? undefined : 2000)}</pre>
      )}
    </div>
  );
});

ShellOutputCard.displayName = 'ShellOutputCard';
