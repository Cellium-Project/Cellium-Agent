import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { API, fetchJSON, postJSON, putJSON } from '../utils/api';
import type { MemoryQueryResponse, MemoryRecord, MemorySummary } from '../types';
import { Icons } from './Icons';
import { CustomDropdown } from './CustomDropdown';

const SCHEMA_OPTIONS = [
  { value: '', label: '全部 schema' },
  { value: 'general', label: 'general' },
  { value: 'profile', label: 'profile' },
  { value: 'project', label: 'project' },
  { value: 'issue', label: 'issue' },
] as const;

const CATEGORY_OPTIONS = [
  { value: '', label: '全部分类' },
  { value: 'general', label: 'general' },
  { value: 'user_info', label: 'user_info' },
  { value: 'preference', label: 'preference' },
  { value: 'project', label: 'project' },
  { value: 'troubleshooting', label: 'troubleshooting' },
  { value: 'command', label: 'command' },
  { value: 'code', label: 'code' },
] as const;

type EditorMode = 'create' | 'edit' | null;

interface MemoryEditorState {
  title: string;
  content: string;
  category: string;
  schema_type: 'general' | 'profile' | 'project' | 'issue';
  tags: string;
  memory_key: string;
  metadataText: string;
  allow_sensitive: boolean;
}

const EMPTY_EDITOR: MemoryEditorState = {
  title: '',
  content: '',
  category: 'general',
  schema_type: 'general',
  tags: '',
  memory_key: '',
  metadataText: '{}',
  allow_sensitive: false,
};

function formatDateTime(value?: string): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function buildEditorState(memory?: MemoryRecord | null): MemoryEditorState {
  if (!memory) return EMPTY_EDITOR;
  return {
    title: memory.title || '',
    content: memory.content || '',
    category: memory.category || 'general',
    schema_type: memory.schema_type || 'general',
    tags: memory.tags || '',
    memory_key: memory.memory_key || '',
    metadataText: JSON.stringify(memory.metadata || {}, null, 2),
    allow_sensitive: !!memory.sensitive,
  };
}

export const MemoryManagerPanel: React.FC = () => {
  const [summary, setSummary] = useState<MemorySummary | null>(null);
  const [items, setItems] = useState<MemoryRecord[]>([]);
  const [query, setQuery] = useState('');
  const [schemaType, setSchemaType] = useState('');
  const [category, setCategory] = useState('');
  const [includeSensitive, setIncludeSensitive] = useState(false);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [limit, setLimit] = useState(20);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [actionKey, setActionKey] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editorMode, setEditorMode] = useState<EditorMode>(null);
  const [editingMemory, setEditingMemory] = useState<MemoryRecord | null>(null);
  const [editor, setEditor] = useState<MemoryEditorState>(EMPTY_EDITOR);

  const summaryCards = useMemo(() => {
    if (!summary) return [];
    return [
      { label: '活跃记忆', value: summary.active_records },
      { label: '总记录数', value: summary.total_records },
      { label: '已删除', value: summary.deleted_records },
      { label: '已遗忘', value: summary.forgotten_records },
      { label: '已合并', value: summary.merged_records },
      { label: '敏感条目', value: summary.sensitive_records },
    ];
  }, [summary]);

  const loadSummary = useCallback(async () => {
    setLoadingSummary(true);
    try {
      const data = await fetchJSON<MemorySummary>(API.memorySummary);
      setSummary(data);
    } catch (err: any) {
      setError(err.message || '加载记忆概览失败');
    } finally {
      setLoadingSummary(false);
    }
  }, []);

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      const trimmed = query.trim();
      if (trimmed) params.set('query', trimmed);
      if (schemaType) params.set('schema_type', schemaType);
      if (category) params.set('category', category);
      if (includeSensitive) params.set('include_sensitive', 'true');
      if (!trimmed && includeDeleted) params.set('include_deleted', 'true');
      params.set('limit', String(limit));
      params.set('offset', String(offset));
      const url = `${API.memories}?${params.toString()}`;
      const data = await fetchJSON<MemoryQueryResponse>(url);
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (err: any) {
      setError(err.message || '加载记忆列表失败');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [category, includeDeleted, includeSensitive, limit, offset, query, schemaType]);

  useEffect(() => {
    setOffset(0);
  }, [query, schemaType, category, includeSensitive, includeDeleted]);

  const refreshAll = useCallback(async () => {
    await Promise.all([loadSummary(), loadItems()]);
  }, [loadItems, loadSummary]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  const openCreate = () => {
    setNotice('');
    setError('');
    setEditingMemory(null);
    setEditor(buildEditorState(null));
    setEditorMode('create');
  };

  const openEdit = (memory: MemoryRecord) => {
    setNotice('');
    setError('');
    setEditingMemory(memory);
    setEditor(buildEditorState(memory));
    setEditorMode('edit');
  };

  const closeEditor = () => {
    setEditorMode(null);
    setEditingMemory(null);
    setEditor(EMPTY_EDITOR);
  };

  const updateEditorField = <K extends keyof MemoryEditorState>(key: K, value: MemoryEditorState[K]) => {
    setEditor(prev => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    if (!editor.title.trim() || !editor.content.trim()) {
      setError('标题和内容不能为空');
      return;
    }

    let metadata: Record<string, any> = {};
    try {
      metadata = editor.metadataText.trim() ? JSON.parse(editor.metadataText) : {};
    } catch {
      setError('metadata 必须是合法 JSON');
      return;
    }

    setActionKey('save');
    setError('');
    setNotice('');
    try {
      const payload = {
        title: editor.title.trim(),
        content: editor.content.trim(),
        category: editor.category || 'general',
        schema_type: editor.schema_type,
        tags: editor.tags,
        memory_key: editor.memory_key,
        metadata,
        allow_sensitive: editor.allow_sensitive,
      };

      if (editorMode === 'edit' && editingMemory) {
        await putJSON(API.memoryDetail(editingMemory.id), payload);
        setNotice(`已更新记忆：${editor.title}`);
      } else {
        await postJSON(API.memories, payload);
        setNotice(`已新增记忆：${editor.title}`);
      }
      await refreshAll();
      closeEditor();
    } catch (err: any) {
      setError(err.message || '保存记忆失败');
    } finally {
      setActionKey('');
    }
  };

  const handleDelete = async (memory: MemoryRecord, mode: 'delete' | 'forget') => {
    const actionLabel = mode === 'delete' ? '删除' : '遗忘';
    if (!window.confirm(`确认${actionLabel}记忆「${memory.title}」吗？`)) {
      return;
    }

    setActionKey(`${mode}:${memory.id}`);
    setError('');
    setNotice('');
    try {
      if (mode === 'forget') {
        await postJSON(API.memoryForget, {
          source: memory.source_file,
          memory_key: memory.memory_key || undefined,
          all_matches: false,
        });
      } else {
        await fetchJSON(API.memoryDetail(memory.id), { method: 'DELETE' });
      }
      setNotice(`已${actionLabel}记忆：${memory.title}`);
      if (editingMemory?.id === memory.id) {
        closeEditor();
      }
      await refreshAll();
    } catch (err: any) {
      setError(err.message || `${actionLabel}记忆失败`);
    } finally {
      setActionKey('');
    }
  };

  const handleMerge = async (memory: MemoryRecord) => {
    if (!memory.memory_key) {
      setError('当前记忆没有 memory_key，无法执行冲突合并');
      return;
    }

    setActionKey(`merge:${memory.id}`);
    setError('');
    setNotice('');
    try {
      const result = await postJSON<{ merged_records?: number }>(API.memoryMerge, {
        memory_key: memory.memory_key,
        schema_type: memory.schema_type,
      });
      setNotice(`冲突合并完成，处理 ${result.merged_records || 0} 条重复记录`);
      await refreshAll();
    } catch (err: any) {
      setError(err.message || '冲突合并失败');
    } finally {
      setActionKey('');
    }
  };

  return (
    <div className="settings-card memory-manager-card">
      <div className="settings-card-header">
        <div className="settings-card-title">
          <Icons.Database size={16} /> 长期记忆管理
        </div>
        <div className="memory-manager-header-actions">
          <button className="btn-secondary btn-sm" onClick={refreshAll} disabled={loading || loadingSummary}>
            {loading || loadingSummary ? '刷新中...' : '刷新'}
          </button>
          <button className="btn-primary btn-sm" onClick={openCreate}>新增记忆</button>
        </div>
      </div>

      <div className="memory-summary-grid">
        {summaryCards.map(card => (
          <div key={card.label} className="memory-summary-card">
            <span className="memory-summary-label">{card.label}</span>
            <strong>{loadingSummary ? '...' : card.value}</strong>
          </div>
        ))}
      </div>

      {summary && (
        <div className="memory-summary-meta">
          <span><strong>目录：</strong>{summary.memory_dir}</span>
          <span><strong>Catalog：</strong>{summary.catalog_file}</span>
        </div>
      )}

      <div className="memory-filter-grid">
        <div className="form-group memory-search-group">
          <label className="settings-field-label">查询</label>
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="输入关键词；留空则按最近更新列出"
            onKeyDown={e => e.key === 'Enter' && loadItems()}
          />
        </div>
        <div className="form-group">
          <label className="settings-field-label">Schema</label>
          <CustomDropdown value={schemaType} items={[...SCHEMA_OPTIONS]} onChange={setSchemaType} />
        </div>
        <div className="form-group">
          <label className="settings-field-label">分类</label>
          <CustomDropdown value={category} items={[...CATEGORY_OPTIONS]} onChange={setCategory} />
        </div>
        <div className="form-group">
          <label className="settings-field-label">返回条数</label>
          <input
            type="number"
            min={1}
            max={200}
            value={limit}
            onChange={e => setLimit(Math.max(1, Math.min(200, Number(e.target.value) || 50)))}
          />
        </div>
      </div>

      <div className="memory-toggle-row">
        <label className="memory-inline-check">
          <input type="checkbox" checked={includeSensitive} onChange={e => setIncludeSensitive(e.target.checked)} />
          <span>显示敏感条目</span>
        </label>
        <label className="memory-inline-check">
          <input type="checkbox" checked={includeDeleted} onChange={e => setIncludeDeleted(e.target.checked)} disabled={!!query.trim()} />
          <span>显示非活跃记录（仅列表模式）</span>
        </label>
        <button className="btn-primary btn-sm" onClick={loadItems} disabled={loading}>
          {loading ? '查询中...' : query.trim() ? '搜索记忆' : '加载列表'}
        </button>
      </div>

      {error && <div className="memory-feedback error">{error}</div>}
      {notice && <div className="memory-feedback success">{notice}</div>}

      {editorMode && (
        <div className="memory-editor-card">
          <div className="memory-editor-header">
            <h4>{editorMode === 'edit' ? '编辑记忆' : '新增记忆'}</h4>
            <button className="btn-icon" onClick={closeEditor} title="关闭">
              <Icons.X size={16} />
            </button>
          </div>
          <div className="memory-editor-grid">
            <div className="form-group">
              <label className="settings-field-label">标题</label>
              <input type="text" value={editor.title} onChange={e => updateEditorField('title', e.target.value)} />
            </div>
            <div className="form-group">
              <label className="settings-field-label">Schema</label>
              <CustomDropdown
                value={editor.schema_type}
                items={SCHEMA_OPTIONS.slice(1).map(item => ({ value: item.value, label: item.label }))}
                onChange={value => updateEditorField('schema_type', value as MemoryEditorState['schema_type'])}
              />
            </div>
            <div className="form-group">
              <label className="settings-field-label">分类</label>
              <CustomDropdown value={editor.category} items={CATEGORY_OPTIONS.slice(1).map(item => ({ value: item.value, label: item.label }))} onChange={value => updateEditorField('category', value)} />
            </div>
            <div className="form-group">
              <label className="settings-field-label">Memory Key</label>
              <input type="text" value={editor.memory_key} onChange={e => updateEditorField('memory_key', e.target.value)} placeholder="如 profile:language" />
            </div>
            <div className="form-group memory-editor-full">
              <label className="settings-field-label">标签</label>
              <input type="text" value={editor.tags} onChange={e => updateEditorField('tags', e.target.value)} placeholder="逗号分隔，如 project,test" />
            </div>
            <div className="form-group memory-editor-full">
              <label className="settings-field-label">内容</label>
              <textarea value={editor.content} onChange={e => updateEditorField('content', e.target.value)} rows={6} />
            </div>
            <div className="form-group memory-editor-full">
              <label className="settings-field-label">Metadata (JSON)</label>
              <textarea value={editor.metadataText} onChange={e => updateEditorField('metadataText', e.target.value)} rows={6} />
            </div>
          </div>
          <label className="memory-inline-check memory-editor-sensitive">
            <input type="checkbox" checked={editor.allow_sensitive} onChange={e => updateEditorField('allow_sensitive', e.target.checked)} />
            <span>允许存储敏感内容（仅在你确定需要时开启）</span>
          </label>
          <div className="memory-editor-actions">
            <button className="btn-secondary" onClick={closeEditor}>取消</button>
            <button className="btn-primary" onClick={handleSave} disabled={actionKey === 'save'}>
              {actionKey === 'save' ? '保存中...' : editorMode === 'edit' ? '保存修改' : '创建记忆'}
            </button>
          </div>
        </div>
      )}

      <div className="memory-results-header">
        <h4>{query.trim() ? '搜索结果' : '最近记忆'}</h4>
        <span>{loading ? '加载中...' : `${items.length} 条 / 共 ${total} 条`}</span>
      </div>

      {items.length === 0 && !loading ? (
        <div className="memory-empty">当前没有可显示的长期记忆。</div>
      ) : (
        <div className="memory-list">
          {items.map(item => (
            <div key={item.id} className="memory-item-card">
              <div className="memory-item-header">
                <div>
                  <div className="memory-item-title-row">
                    <h4>{item.title || '未命名记忆'}</h4>
                    <span className={`memory-status-pill ${item.status}`}>{item.status}</span>
                    <span className="memory-status-pill schema">{item.schema_type}</span>
                    <span className="memory-status-pill category">{item.category}</span>
                    {item.sensitive && <span className="memory-status-pill sensitive">敏感</span>}
                  </div>
                  <div className="memory-item-meta">
                    <span>ID: {item.id}</span>
                    <span>来源: {item.source_file || '-'}</span>
                    <span>更新: {formatDateTime(item.updated_at)}</span>
                    {query.trim() && <span>评分: {item.score?.toFixed(4) || '0.0000'}</span>}
                  </div>
                  <div className="memory-item-meta">
                    <span>memory_key: {item.memory_key || '-'}</span>
                    <span>版本: {item.revisions}</span>
                    {item.sensitivity_reason && <span>脱敏: {item.sensitivity_reason}</span>}
                  </div>
                </div>
                <div className="memory-item-actions">
                  <button className="btn-secondary btn-sm" onClick={() => openEdit(item)}>编辑</button>
                  {item.memory_key && item.status === 'active' && (
                    <button className="btn-secondary btn-sm" onClick={() => handleMerge(item)} disabled={actionKey === `merge:${item.id}`}>
                      {actionKey === `merge:${item.id}` ? '合并中...' : '合并冲突'}
                    </button>
                  )}
                  {item.status === 'active' && (
                    <button className="btn-secondary btn-sm" onClick={() => handleDelete(item, 'forget')} disabled={actionKey === `forget:${item.id}`}>
                      {actionKey === `forget:${item.id}` ? '处理中...' : '遗忘'}
                    </button>
                  )}
                  {item.status !== 'deleted' && (
                    <button className="btn-danger btn-sm" onClick={() => handleDelete(item, 'delete')} disabled={actionKey === `delete:${item.id}`}>
                      {actionKey === `delete:${item.id}` ? '删除中...' : '删除'}
                    </button>
                  )}
                </div>
              </div>

              <pre className="memory-item-content">{item.content}</pre>

              {item.tags && (
                <div className="memory-tag-list">
                  {item.tags.split(',').map(tag => tag.trim()).filter(Boolean).map(tag => (
                    <span key={`${item.id}-${tag}`} className="memory-tag">{tag}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {total > limit && (
        <div className="memory-pagination">
          <span className="memory-pagination-info">
            第 {offset + 1}-{Math.min(offset + items.length, total)} 条 / 共 {total} 条
          </span>
          <div className="memory-pagination-controls">
            <button
              className="btn-secondary btn-sm"
              onClick={() => setOffset(Math.max(0, offset - limit))}
              disabled={offset === 0 || loading}
            >
              上一页
            </button>
            <span className="memory-pagination-page">
              {Math.floor(offset / limit) + 1} / {Math.ceil(total / limit)}
            </span>
            <button
              className="btn-secondary btn-sm"
              onClick={() => setOffset(offset + limit)}
              disabled={offset + limit >= total || loading}
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
