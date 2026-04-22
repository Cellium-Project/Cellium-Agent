import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { API, fetchJSON, putJSON, postJSON, deleteJSON } from '../utils/api';
import { Icons } from './Icons';

interface Gene {
  id: string;
  task_type: string;
  title: string;
  content: string;
  version: number;
  usage_count: number;
  success_count: number;
  failure_count: number;
  success_rate: number;
  avg_reward: number;
  avg_duration_ms: number;
  consecutive_success: number;
  consecutive_failure: number;
  evolution_history: Array<{
    version: number;
    change: string;
    at: string;
  }>;
  recent_results: Array<{
    success: boolean;
    reward: number;
    duration_ms: number;
    at: string;
  }>;
  created_at?: string;
  updated_at?: string;
}

interface GeneStats {
  total_genes: number;
  total_usage: number;
  avg_success_rate: number;
  evolved_genes: number;
}

function useSavingState() {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  const doSave = useCallback(async (saveFn: () => Promise<any>) => {
    setSaving(true);
    setError('');
    try {
      const result = await saveFn();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      return result;
    } catch (e: any) {
      setError(e.message || '操作失败');
      throw e;
    } finally {
      setSaving(false);
    }
  }, []);

  return { saving, saved, error, doSave };
}

export const GeneManagerTab: React.FC = () => {
  const { t } = useTranslation();
  const [genes, setGenes] = useState<Gene[]>([]);
  const [stats, setStats] = useState<GeneStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [filterQuery, setFilterQuery] = useState('');
  const [selectedGene, setSelectedGene] = useState<Gene | null>(null);
  const [editContent, setEditContent] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const [isClosing, setIsClosing] = useState(false);

  const { saving, saved, error, doSave } = useSavingState();

  const loadGenes = useCallback(async () => {
    setLoading(true);
    try {
      const [genesData, statsData] = await Promise.all([
        fetchJSON<{ items: Gene[]; total: number }>(API.genes),
        fetchJSON<GeneStats>(API.geneStats),
      ]);
      setGenes(genesData.items || []);
      setStats(statsData);
    } catch (e: any) {
      console.error('Failed to load genes:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadGenes();
  }, [loadGenes]);

  const filteredGenes = useMemo(() => {
    if (!filterQuery.trim()) return genes;
    const query = filterQuery.toLowerCase();
    return genes.filter(
      (gene) =>
        gene.task_type.toLowerCase().includes(query) ||
        gene.title.toLowerCase().includes(query) ||
        gene.content.toLowerCase().includes(query)
    );
  }, [genes, filterQuery]);

  const handleSelectGene = useCallback((gene: Gene) => {
    setSelectedGene(gene);
    setEditContent(gene.content);
    setIsEditing(false);
    setIsClosing(false);
    setShowDetail(true);
  }, []);

  const handleSave = useCallback(async () => {
    if (!selectedGene) return;
    await doSave(async () => {
      const updated = await putJSON<Gene>(API.geneDetail(selectedGene.id), {
        content: editContent,
        title: selectedGene.title,
      });
      setSelectedGene(updated);
      setGenes((prev) =>
        prev.map((g) => (g.id === updated.id ? updated : g))
      );
      setIsEditing(false);
    });
  }, [selectedGene, editContent, doSave]);

  const handleEvolve = useCallback(async () => {
    if (!selectedGene) return;
    await doSave(async () => {
      await postJSON(API.geneEvolve(selectedGene.id), {});
      await loadGenes();
      const refreshed = await fetchJSON<Gene>(API.geneDetail(selectedGene.id));
      setSelectedGene(refreshed);
    });
  }, [selectedGene, loadGenes, doSave]);

  const handleDelete = useCallback(async () => {
    if (!selectedGene) return;
    await doSave(async () => {
      await deleteJSON<{ success: boolean; deleted_id: string }>(API.geneDelete(selectedGene.id));
      handleCloseDetail();
      await loadGenes();
    });
  }, [selectedGene, loadGenes, doSave]);

  const handleCloseDetail = () => {
    setIsClosing(true);
    setTimeout(() => {
      setShowDetail(false);
      setIsClosing(false);
      setIsEditing(false);
    }, 150);
  };

  const formatSuccessRate = (rate: number) => {
    return `${(rate * 100).toFixed(1)}%`;
  };

  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  const getRateClass = (rate: number) => {
    if (rate >= 0.7) return 'good';
    if (rate >= 0.4) return 'medium';
    return 'poor';
  };

  return (
    <div className="settings-panel">
      <div className="skill-header">
        <h3>{t('settings.tabs.gene')}</h3>
      </div>

      {error && <div className="memory-feedback error">{error}</div>}
      {saved && <div className="memory-feedback success">{t('gene.operationSuccess')}</div>}

      {stats && (
        <div className="settings-section">
          <div className="memory-summary-grid">
            <div className="memory-summary-card">
              <strong>{stats.total_genes}</strong>
              <span className="memory-summary-label">{t('gene.stats.totalGenes')}</span>
            </div>
            <div className="memory-summary-card">
              <strong>{stats.total_usage}</strong>
              <span className="memory-summary-label">{t('gene.stats.totalUsage')}</span>
            </div>
            <div className="memory-summary-card">
              <strong>{formatSuccessRate(stats.avg_success_rate)}</strong>
              <span className="memory-summary-label">{t('gene.stats.avgSuccessRate')}</span>
            </div>
            <div className="memory-summary-card">
              <strong>{stats.evolved_genes}</strong>
              <span className="memory-summary-label">{t('gene.stats.evolvedGenes')}</span>
            </div>
          </div>
        </div>
      )}

      <div className="settings-section">
        <div className="form-group">
          <input
            type="text"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder={t('gene.searchPlaceholder')}
          />
        </div>
      </div>

      <div className="settings-section">
        <h4>{t('gene.listTitle')} ({genes.length})</h4>
        {loading ? (
          <div className="loading-indicator">{t('common.loading')}</div>
        ) : filteredGenes.length === 0 ? (
          <div className="memory-empty">{filterQuery ? t('gene.noMatch') : t('gene.empty')}</div>
        ) : (
          filteredGenes.map((gene) => (
            <div key={gene.id} className="model-card">
              <div className="model-card-header">
                <div className="skill-info">
                  <span className="skill-name">{gene.task_type}</span>
                  <span className="memory-status-pill category">v{gene.version}</span>
                  {gene.evolution_history.length > 1 && (
                    <span className="memory-status-pill schema">
                      <Icons.Zap size={10} />
                      {t('gene.evolved')}
                    </span>
                  )}
                </div>
                <div className="skill-actions">
                  <button className="btn-link" onClick={() => handleSelectGene(gene)}>
                    {t('gene.viewDetail')}
                  </button>
                </div>
              </div>
              <div className="model-card-body">
                <div className="skill-desc">{gene.title}</div>
                <div className="memory-item-meta">
                  <span className="memory-status-pill">
                    <Icons.Activity size={10} />
                    {gene.usage_count}
                  </span>
                  <span className={`memory-status-pill ${getRateClass(gene.success_rate)}`}>
                    {formatSuccessRate(gene.success_rate)}
                  </span>
                  {gene.consecutive_failure >= 3 && (
                    <span className="memory-status-pill sensitive">
                      <Icons.AlertTriangle size={10} />
                      {t('gene.consecutiveFailureWarning', { count: gene.consecutive_failure })}
                    </span>
                  )}
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {showDetail && selectedGene && createPortal(
        <div className={`skill-detail-modal ${isClosing ? 'closing' : ''}`}>
          <div className="modal-overlay" onClick={handleCloseDetail} />
          <div className="modal-content">
            <div className="modal-header">
              <h3>{selectedGene.task_type}</h3>
              <button className="btn-close" onClick={handleCloseDetail}>
                <Icons.Close />
              </button>
            </div>
            <div className="modal-body">
              <div className="memory-summary-grid" style={{ marginBottom: '20px' }}>
                <div className="memory-summary-card">
                  <strong>{selectedGene.usage_count}</strong>
                  <span className="memory-summary-label">{t('gene.usageCount')}</span>
                </div>
                <div className="memory-summary-card">
                  <strong>{formatSuccessRate(selectedGene.success_rate)}</strong>
                  <span className="memory-summary-label">{t('gene.successRate')}</span>
                </div>
                <div className="memory-summary-card">
                  <strong>{selectedGene.avg_reward.toFixed(2)}</strong>
                  <span className="memory-summary-label">{t('gene.avgReward')}</span>
                </div>
                <div className="memory-summary-card">
                  <strong>{formatDuration(selectedGene.avg_duration_ms)}</strong>
                  <span className="memory-summary-label">{t('gene.avgDuration')}</span>
                </div>
              </div>

              {selectedGene.recent_results.length > 0 && (
                <div className="detail-row">
                  <label>{t('gene.recentResults')}</label>
                  <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', marginTop: '8px' }}>
                    {selectedGene.recent_results.slice(0, 20).map((result, idx) => (
                      <div
                        key={idx}
                        className={`gene-result-dot ${result.success ? 'success' : 'failure'}`}
                        title={`${result.success ? t('gene.success') : t('gene.failure')} - ${result.reward.toFixed(2)}`}
                      />
                    ))}
                  </div>
                </div>
              )}

              <div className="detail-row">
                <label>{t('gene.content')}</label>
                {isEditing ? (
                  <textarea
                    className="gene-content-editor"
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    rows={16}
                    style={{
                      width: '100%',
                      marginTop: '8px',
                      padding: '12px',
                      borderRadius: '8px',
                      border: '1px solid var(--border-primary)',
                      background: 'var(--bg-secondary)',
                      color: 'var(--text-primary)',
                      fontFamily: 'Consolas, Monaco, monospace',
                      fontSize: '13px',
                      resize: 'vertical'
                    }}
                  />
                ) : (
                  <pre style={{
                    marginTop: '8px',
                    padding: '12px',
                    background: 'var(--bg-tertiary)',
                    borderRadius: '8px',
                    border: '1px solid var(--border-primary)',
                    fontSize: '13px',
                    lineHeight: '1.6',
                    maxHeight: '300px',
                    overflow: 'auto',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word'
                  }}>{selectedGene.content}</pre>
                )}
              </div>

              {selectedGene.evolution_history.length > 0 && (
                <div className="detail-row">
                  <label>{t('gene.evolutionHistory')}</label>
                  <div style={{ marginTop: '8px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {selectedGene.evolution_history.map((entry, idx) => (
                      <div key={idx} style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '12px',
                        padding: '10px 12px',
                        background: 'var(--bg-tertiary)',
                        border: '1px solid var(--border-primary)',
                        borderRadius: '6px'
                      }}>
                        <span className="memory-status-pill">v{entry.version}</span>
                        <span style={{ flex: 1, fontSize: '13px' }}>{entry.change}</span>
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                          {new Date(entry.at).toLocaleString()}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="form-actions" style={{ marginTop: '20px', paddingTop: '16px', borderTop: '1px solid var(--border-primary)', display: 'flex', justifyContent: 'space-between' }}>
                <button className="btn-danger" onClick={handleDelete} disabled={saving} style={{ minWidth: '80px' }}>
                  <Icons.Trash2 size={14} />
                  {t('common.delete')}
                </button>
                <div style={{ display: 'flex', gap: '12px' }}>
                  {!isEditing ? (
                    <>
                      <button className="btn-secondary" onClick={() => setIsEditing(true)}>
                        <Icons.Edit2 size={14} />
                        {t('gene.edit')}
                      </button>
                      <button className="btn-primary" onClick={handleEvolve} disabled={saving}>
                        <Icons.Zap size={14} />
                        {t('gene.evolve')}
                      </button>
                    </>
                  ) : (
                    <>
                      <button className="btn-secondary" onClick={() => {
                        setIsEditing(false);
                        setEditContent(selectedGene.content);
                      }}>
                        {t('common.cancel')}
                      </button>
                      <button className="btn-primary" onClick={handleSave} disabled={saving}>
                        {saving ? <Icons.Loader2 size={14} className="spin" /> : <Icons.Check size={14} />}
                        {saved ? t('common.saved') : t('common.save')}
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};
