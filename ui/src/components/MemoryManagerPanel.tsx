import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { API, fetchJSON, postJSON, putJSON } from '../utils/api';
import type { MemoryQueryResponse, MemoryRecord, MemorySummary } from '../types';
import { Icons } from './Icons';
import { CustomDropdown } from './CustomDropdown';

const useSchemaOptions = () => {
  const { t } = useTranslation();
  return [
    { value: '', label: t('memoryManager.allSchemas') },
    { value: 'general', label: 'general' },
    { value: 'profile', label: 'profile' },
    { value: 'project', label: 'project' },
    { value: 'issue', label: 'issue' },
  ] as const;
};

const useCategoryOptions = () => {
  const { t } = useTranslation();
  return [
    { value: '', label: t('memoryManager.allCategories') },
    { value: 'general', label: 'general' },
    { value: 'user_info', label: t('memoryManager.userInfo') },
    { value: 'preference', label: t('memoryManager.preference') },
    { value: 'project', label: 'project' },
    { value: 'troubleshooting', label: t('memoryManager.troubleshooting') },
    { value: 'command', label: t('memoryManager.command') },
    { value: 'code', label: t('memoryManager.code') },
  ] as const;
};

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

interface ArchiveEntry {
  id: string;
  time: string;
  session_id: string;
  messages: Array<{
    role: string;
    content: string;
    timestamp?: string;
    tool_calls?: any[];
  }>;
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

function formatDateTime(value?: string, t?: (key: string) => string): string {
  if (!value) return t ? t('common.dash') : '-';
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
  const { t } = useTranslation();
  const SCHEMA_OPTIONS = useSchemaOptions();
  const CATEGORY_OPTIONS = useCategoryOptions();
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
  const [archiveModal, setArchiveModal] = useState<{ open: boolean; entry: ArchiveEntry | null; loading: boolean; error: string }>({ open: false, entry: null, loading: false, error: '' });
  const [isArchiveClosing, setIsArchiveClosing] = useState(false);

  const summaryCards = useMemo(() => {
    if (!summary) return [];
    return [
      { label: t('memoryManager.activeRecords'), value: summary.active_records },
      { label: t('memoryManager.totalRecords'), value: summary.total_records },
      { label: t('memoryManager.deleted'), value: summary.deleted_records },
      { label: t('memoryManager.forgotten'), value: summary.forgotten_records },
      { label: t('memoryManager.merged'), value: summary.merged_records },
      { label: t('memoryManager.sensitive'), value: summary.sensitive_records },
    ];
  }, [summary, t]);

  const loadSummary = useCallback(async () => {
    setLoadingSummary(true);
    try {
      const data = await fetchJSON<MemorySummary>(API.memorySummary);
      setSummary(data);
    } catch (err: any) {
      setError(err.message || t('memoryManager.loadSummaryFailed'));
    } finally {
      setLoadingSummary(false);
    }
  }, [t]);

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
      setError(err.message || t('memoryManager.loadItemsFailed'));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [category, includeDeleted, includeSensitive, limit, offset, query, schemaType, t]);

  useEffect(() => {
    setOffset(0);
  }, [query, schemaType, category, includeSensitive, includeDeleted]);

  const refreshAll = useCallback(async () => {
    await Promise.all([loadSummary(), loadItems()]);
  }, [loadItems, loadSummary]);

  const handleSave = async () => {
    if (!editor.title.trim() || !editor.content.trim()) {
      setError(t('memoryManager.titleContentRequired'));
      return;
    }

    let metadata: Record<string, any> = {};
    try {
      metadata = editor.metadataText.trim() ? JSON.parse(editor.metadataText) : {};
    } catch {
      setError(t('memoryManager.invalidMetadata'));
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
        setNotice(t('memoryManager.updatedSuccess', { title: editor.title }));
      } else {
        await postJSON(API.memories, payload);
        setNotice(t('memoryManager.createdSuccess', { title: editor.title }));
      }
      await refreshAll();
      closeEditor();
    } catch (err: any) {
      setError(err.message || t('memoryManager.saveFailed'));
    } finally {
      setActionKey('');
    }
  };

  const handleDelete = async (memory: MemoryRecord, mode: 'delete' | 'forget') => {
    const actionLabel = mode === 'delete' ? t('common.delete') : t('memoryManager.forget');
    if (!window.confirm(t('memoryManager.confirmAction', { action: actionLabel, title: memory.title }))) {
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
      setNotice(t('memoryManager.actionCompleted', { action: actionLabel, title: memory.title }));
      if (editingMemory?.id === memory.id) {
        closeEditor();
      }
      await refreshAll();
    } catch (err: any) {
      setError(err.message || t('memoryManager.actionFailed', { action: actionLabel }));
    } finally {
      setActionKey('');
    }
  };

  const handleMerge = async (memory: MemoryRecord) => {
    if (!memory.memory_key) {
      setError(t('memoryManager.noMemoryKey'));
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
      setNotice(t('memoryManager.mergeCompleted', { count: result.merged_records || 0 }));
      await refreshAll();
    } catch (err: any) {
      setError(err.message || t('memoryManager.mergeFailed'));
    } finally {
      setActionKey('');
    }
  };

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

  const openArchiveModal = async (entryId: string) => {
    setIsArchiveClosing(false);
    setArchiveModal({ open: true, entry: null, loading: true, error: '' });
    try {
      const entry = await fetchJSON<ArchiveEntry>(API.memoryArchive(entryId));
      setArchiveModal({ open: true, entry, loading: false, error: '' });
    } catch (err: any) {
      setArchiveModal({ open: true, entry: null, loading: false, error: err.message || t('memoryManager.loadArchiveFailed') });
    }
  };

  const closeArchiveModal = () => {
    setIsArchiveClosing(true);
    setTimeout(() => {
      setArchiveModal({ open: false, entry: null, loading: false, error: '' });
      setIsArchiveClosing(false);
    }, 150);
  };

  const closeEditor = () => {
    setEditorMode(null);
    setEditingMemory(null);
    setEditor(EMPTY_EDITOR);
  };

  const updateEditorField = <K extends keyof MemoryEditorState>(key: K, value: MemoryEditorState[K]) => {
    setEditor(prev => ({ ...prev, [key]: value }));
  };

  return (
    <div className="settings-card memory-manager-card">
      <div className="settings-card-header">
        <div className="settings-card-title">
          <Icons.Database size={16} /> {t('memoryManager.longTermMemory')}
        </div>
        <div className="memory-manager-header-actions">
          <button className="btn-secondary btn-sm" onClick={refreshAll} disabled={loading || loadingSummary}>
            {loading || loadingSummary ? t('common.refreshing') : t('common.refresh')}
          </button>
          <button className="btn-primary btn-sm" onClick={openCreate}>{t('memoryManager.createMemory')}</button>
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
          <span><strong>{t('memoryManager.directory')}：</strong>{summary.memory_dir}</span>
          <span><strong>{t('memoryManager.catalog')}：</strong>{summary.catalog_file}</span>
        </div>
      )}

      <div className="memory-filter-grid">
        <div className="form-group memory-search-group">
          <label className="settings-field-label">{t('memoryManager.query')}</label>
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={t('memoryManager.queryPlaceholder')}
            onKeyDown={e => e.key === 'Enter' && loadItems()}
          />
        </div>
        <div className="form-group">
          <label className="settings-field-label">{t('memoryManager.schema')}</label>
          <CustomDropdown value={schemaType} items={[...SCHEMA_OPTIONS]} onChange={setSchemaType} />
        </div>
        <div className="form-group">
          <label className="settings-field-label">{t('memoryManager.category')}</label>
          <CustomDropdown value={category} items={[...CATEGORY_OPTIONS]} onChange={setCategory} />
        </div>
        <div className="form-group">
          <label className="settings-field-label">{t('memoryManager.limit')}</label>
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
          <span>{t('memoryManager.showSensitive')}</span>
        </label>
        <label className="memory-inline-check">
          <input type="checkbox" checked={includeDeleted} onChange={e => setIncludeDeleted(e.target.checked)} disabled={!!query.trim()} />
          <span>{t('memoryManager.showInactive')}</span>
        </label>
        <button className="btn-primary btn-sm" onClick={loadItems} disabled={loading}>
          {loading ? t('memoryManager.searching') : query.trim() ? t('memoryManager.search') : t('memoryManager.loadList')}
        </button>
      </div>

      {error && <div className="memory-feedback error">{error}</div>}
      {notice && <div className="memory-feedback success">{notice}</div>}

      {editorMode && (
        <div className="memory-editor-card">
          <div className="memory-editor-header">
            <h4>{editorMode === 'edit' ? t('memoryManager.editMemory') : t('memoryManager.createMemory')}</h4>
            <button className="btn-icon" onClick={closeEditor} title={t('common.close')}>
              <Icons.X size={16} />
            </button>
          </div>
          <div className="memory-editor-grid">
            <div className="form-group">
              <label className="settings-field-label">{t('memoryManager.title')}</label>
              <input type="text" value={editor.title} onChange={e => updateEditorField('title', e.target.value)} />
            </div>
            <div className="form-group">
              <label className="settings-field-label">{t('memoryManager.schema')}</label>
              <CustomDropdown
                value={editor.schema_type}
                items={SCHEMA_OPTIONS.slice(1).map(item => ({ value: item.value, label: item.label }))}
                onChange={value => updateEditorField('schema_type', value as MemoryEditorState['schema_type'])}
              />
            </div>
            <div className="form-group">
              <label className="settings-field-label">{t('memoryManager.category')}</label>
              <CustomDropdown value={editor.category} items={CATEGORY_OPTIONS.slice(1).map(item => ({ value: item.value, label: item.label }))} onChange={value => updateEditorField('category', value)} />
            </div>
            <div className="form-group">
              <label className="settings-field-label">{t('memoryManager.memoryKeyLabel')}</label>
              <input type="text" value={editor.memory_key} onChange={e => updateEditorField('memory_key', e.target.value)} placeholder={t('memoryManager.memoryKeyPlaceholder')} />
            </div>
            <div className="form-group memory-editor-full">
              <label className="settings-field-label">{t('memoryManager.tags')}</label>
              <input type="text" value={editor.tags} onChange={e => updateEditorField('tags', e.target.value)} placeholder={t('memoryManager.tagsPlaceholder')} />
            </div>
            <div className="form-group memory-editor-full">
              <label className="settings-field-label">{t('memoryManager.content')}</label>
              <textarea value={editor.content} onChange={e => updateEditorField('content', e.target.value)} rows={6} />
            </div>
            <div className="form-group memory-editor-full">
              <label className="settings-field-label">{t('memoryManager.metadataJson')}</label>
              <textarea value={editor.metadataText} onChange={e => updateEditorField('metadataText', e.target.value)} rows={6} />
            </div>
          </div>
          <label className="memory-inline-check memory-editor-sensitive">
            <input type="checkbox" checked={editor.allow_sensitive} onChange={e => updateEditorField('allow_sensitive', e.target.checked)} />
            <span>{t('memoryManager.allowSensitiveDesc')}</span>
          </label>
          <div className="memory-editor-actions">
            <button className="btn-secondary" onClick={closeEditor}>{t('common.cancel')}</button>
            <button className="btn-primary" onClick={handleSave} disabled={actionKey === 'save'}>
              {actionKey === 'save' ? t('common.saving') : editorMode === 'edit' ? t('memoryManager.saveChanges') : t('memoryManager.create')}
            </button>
          </div>
        </div>
      )}

      <div className="memory-results-header">
        <h4>{query.trim() ? t('memoryManager.searchResults') : t('memoryManager.recentMemories')}</h4>
        <span>{loading ? t('common.loading') : t('memoryManager.itemsCount', { count: items.length, total })}</span>
      </div>

      {items.length === 0 && !loading ? (
        <div className="memory-empty">{t('memoryManager.noMemories')}</div>
      ) : (
        <div className="memory-list">
          {items.map(item => (
            <div key={item.id} className="memory-item-card">
              <div className="memory-item-header">
                <div>
                  <div className="memory-item-title-row">
                    <h4>{item.title || t('memoryManager.untitled')}</h4>
                    <span className={`memory-status-pill ${item.status}`}>{item.status}</span>
                    <span className="memory-status-pill schema">{item.schema_type}</span>
                    <span className="memory-status-pill category">{item.category}</span>
                    {item.sensitive && <span className="memory-status-pill sensitive">{t('memoryManager.sensitive')}</span>}
                  </div>
                  <div className="memory-item-meta">
                    <span>ID: {item.id}</span>
                    <span>{t('memoryManager.source')}: {item.source_file || t('common.dash')}</span>
                    <span>{t('memoryManager.updated')}: {formatDateTime(item.updated_at, t)}</span>
                    {query.trim() && <span>{t('memoryManager.score')}: {item.score?.toFixed(4) || '0.0000'}</span>}
                  </div>
                  <div className="memory-item-meta">
                    <span>memory_key: {item.memory_key || t('common.dash')}</span>
                    <span>{t('memoryManager.revisions')}: {item.revisions}</span>
                    {item.sensitivity_reason && <span>{t('memoryManager.desensitized')}: {item.sensitivity_reason}</span>}
                    {(item.metadata?.archive_entry_id || item.metadata?.archive_id) && (
                      <span className="memory-archive-link">
                        <button
                          className="btn-link btn-sm"
                          onClick={() => openArchiveModal(item.metadata?.archive_entry_id || item.metadata?.archive_id)}
                        >
                          {t('memoryManager.viewArchive')} →
                        </button>
                      </span>
                    )}
                  </div>
                </div>
                <div className="memory-item-actions">
                  <button className="btn-secondary btn-sm" onClick={() => openEdit(item)}>{t('common.edit')}</button>
                  {item.memory_key && item.status === 'active' && (
                    <button className="btn-secondary btn-sm" onClick={() => handleMerge(item)} disabled={actionKey === `merge:${item.id}`}>
                      {actionKey === `merge:${item.id}` ? t('memoryManager.merging') : t('memoryManager.merge')}
                    </button>
                  )}
                  {item.status === 'active' && (
                    <button className="btn-secondary btn-sm" onClick={() => handleDelete(item, 'forget')} disabled={actionKey === `forget:${item.id}`}>
                      {actionKey === `forget:${item.id}` ? t('memoryManager.processing') : t('memoryManager.forget')}
                    </button>
                  )}
                  {item.status !== 'deleted' && (
                    <button className="btn-danger btn-sm" onClick={() => handleDelete(item, 'delete')} disabled={actionKey === `delete:${item.id}`}>
                      {actionKey === `delete:${item.id}` ? t('common.deleting') : t('common.delete')}
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
            {t('memoryManager.paginationInfo', { start: offset + 1, end: Math.min(offset + items.length, total), total })}
          </span>
          <div className="memory-pagination-controls">
            <button
              className="btn-secondary btn-sm"
              onClick={() => setOffset(Math.max(0, offset - limit))}
              disabled={offset === 0 || loading}
            >
              {t('common.prevPage')}
            </button>
            <span className="memory-pagination-page">
              {Math.floor(offset / limit) + 1} / {Math.ceil(total / limit)}
            </span>
            <button
              className="btn-secondary btn-sm"
              onClick={() => setOffset(offset + limit)}
              disabled={offset + limit >= total || loading}
            >
              {t('common.nextPage')}
            </button>
          </div>
        </div>
      )}

      {/* Archive Modal */}
      {archiveModal.open && createPortal(
        <div className={`archive-detail-modal ${isArchiveClosing ? 'closing' : ''}`}>
          <div className="modal-overlay" onClick={closeArchiveModal} />
          <div className="modal-content archive-modal">
            <div className="modal-header">
              <h3>{t('memoryManager.archiveDetail')}</h3>
              <button className="btn-close" onClick={closeArchiveModal}>
                <Icons.X size={20} />
              </button>
            </div>
            <div className="modal-body">
              {archiveModal.loading && (
                <div className="archive-loading">{t('common.loading')}</div>
              )}
              {archiveModal.error && (
                <div className="archive-error">{archiveModal.error}</div>
              )}
              {archiveModal.entry && (
                <div className="archive-content">
                  <div className="archive-meta">
                    <span><strong>ID:</strong> {archiveModal.entry.id}</span>
                    <span><strong>{t('memoryManager.session')}:</strong> {archiveModal.entry.session_id}</span>
                    <span><strong>{t('memoryManager.time')}:</strong> {formatDateTime(archiveModal.entry.time, t)}</span>
                  </div>
                  <div className="archive-messages">
                    {archiveModal.entry.messages?.map((msg, idx) => (
                      <div key={idx} className={`archive-message ${msg.role}`}>
                        <div className="archive-message-role">{msg.role}</div>
                        <pre className="archive-message-content">{msg.content}</pre>
                        {msg.tool_calls && (
                          <div className="archive-message-tools">
                            <strong>{t('memoryManager.toolCalls')}:</strong>
                            {msg.tool_calls.map((tool: any, tidx: number) => (
                              <div key={tidx} className="archive-tool-call">
                                <code>{tool.function?.name}</code>: {tool.function?.arguments}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};
