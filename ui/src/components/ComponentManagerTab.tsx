import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { API, fetchJSON, postJSON } from '../utils/api';
import { Icons } from './Icons';
import { CustomDropdown } from './CustomDropdown';
import { ConfirmDialog } from './ConfirmDialog';

interface ComponentInfo {
  cell_name: string;
  class: string;
  commands: Record<string, string>;
  command_count: number;
  source_file?: string;
  is_builtin?: boolean;
  removable?: boolean;
}

interface DiscoveredInfo {
  file: string;
  class_name: string;
  module_path: string;
  is_new: boolean;
  is_enabled: boolean;
  is_loaded: boolean;
}

interface MarketItem {
  slug: string;
  name: string;
  summary: string;
  category: string;
  tags: string[];
  author: string;
  latest_version: string;
  updated_at: string;
  deprecated: boolean;
  installed: boolean;
}

const CATEGORIES = ['', 'web', 'ai', 'security', 'interaction', 'automation', 'data', 'media', 'other'];

export const ComponentManagerTab: React.FC = () => {
  const { t } = useTranslation();
  const [components, setComponents] = useState<ComponentInfo[]>([]);
  const [discovered, setDiscovered] = useState<DiscoveredInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [filter, setFilter] = useState('');

  const [keyword, setKeyword] = useState('');
  const [category, setCategory] = useState('');
  const [marketItems, setMarketItems] = useState<MarketItem[]>([]);
  const [marketTotal, setMarketTotal] = useState(0);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketError, setMarketError] = useState('');
  const [installing, setInstalling] = useState('');
  const [detail, setDetail] = useState<any>(null);
  const [detailClosing, setDetailClosing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const [unloadTarget, setUnloadTarget] = useState('');
  const [depsDialog, setDepsDialog] = useState<{
    component: string;
    specs: string[];
    phase: 'confirm' | 'installing' | 'reloading' | 'error';
    error?: string;
  } | null>(null);

  const flash = (msg: string) => {
    setNotice(msg);
    setTimeout(() => setNotice(''), 2000);
  };

  const loadLocal = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [loaded, scan] = await Promise.all([
        fetchJSON<{ components: ComponentInfo[] }>(API.components),
        fetchJSON<{ all: DiscoveredInfo[] }>(API.componentsScan),
      ]);
      setComponents(loaded.components || []);
      setDiscovered((scan.all || []).filter((i) => !i.is_loaded));
    } catch (e: any) {
      setError(e.message || t('component.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  const searchMarket = useCallback(async (page = 1) => {
    setMarketLoading(true);
    setMarketError('');
    try {
      const params = new URLSearchParams({
        keyword,
        category,
        page: String(page),
        page_size: '12',
      });
      const data = await fetchJSON<any>(`${API.componentMarketSearch}?${params}`);
      if (data.success === false) {
        setMarketError(data.error || t('component.installFailed'));
        setMarketItems([]);
        setMarketTotal(0);
      } else {
        setMarketItems(data.items || []);
        setMarketTotal(data.total || 0);
      }
    } catch (e: any) {
      setMarketError(e.message || t('component.installFailed'));
    } finally {
      setMarketLoading(false);
    }
  }, [keyword, category, t]);

  useEffect(() => {
    loadLocal();
  }, [loadLocal]);

  useEffect(() => {
    const timer = setTimeout(() => searchMarket(1), 300);
    return () => clearTimeout(timer);
  }, [searchMarket]);

  const filtered = useMemo(() => {
    if (!filter.trim()) return components;
    const q = filter.toLowerCase();
    return components.filter((c) => c.cell_name.toLowerCase().includes(q));
  }, [components, filter]);

  const handleReload = async () => {
    setBusy('reload');
    setError('');
    try {
      await postJSON(API.componentsReload, {});
      await loadLocal();
      flash(t('component.operationSuccess'));
    } catch (e: any) {
      setError(e.message || t('component.loadFailed'));
    } finally {
      setBusy('');
    }
  };

  const handleLoad = async (name: string) => {
    setBusy(`load:${name}`);
    setError('');
    try {
      const data = await fetchJSON<any>(API.componentLoad(name), { method: 'POST' });
      if (data.status !== 'ok') throw new Error(data.message || t('component.loadFailed'));
      await loadLocal();
      flash(t('component.operationSuccess'));
    } catch (e: any) {
      const specs: string[] = e?.payload?.dependencies?.targets || [];
      if (specs.length) {
        setError('');
        promptDeps(specs, e.payload?.component || name);
      } else {
        setError(e.message || t('component.loadFailed'));
      }
    } finally {
      setBusy('');
    }
  };

  const promptDeps = (specs: string[], component: string) => {
    if (!specs || !specs.length) return;
    setDepsDialog({ component, specs, phase: 'confirm' });
  };

  const handleDepsResult = (data: any) => {
    const deps = data?.dependencies;
    const specs: string[] = deps?.targets || [];
    if (specs.length) {
      promptDeps(specs, data?.name || data?.component || '');
    } else if (deps?.install_command) {
      setError(t('component.depsMissing', { cmd: deps.install_command }));
    }
  };

  const runDepsInstall = async () => {
    if (!depsDialog) return;
    const { component, specs } = depsDialog;
    setDepsDialog({ ...depsDialog, phase: 'installing', error: '' });
    try {
      await postJSON(API.componentDepsInstall, { specs });
    } catch (e: any) {
      setDepsDialog({ ...depsDialog, phase: 'error', error: e.message });
      return;
    }
    setDepsDialog({ ...depsDialog, phase: 'reloading' });
    try {
      const res = await fetchJSON<any>(API.componentLoad(component), { method: 'POST' });
      if (res.status !== 'ok') throw new Error(res.message || t('component.loadFailed'));
      setDepsDialog(null);
      flash(t('component.depsDone', { name: component }));
      await loadLocal();
    } catch (e: any) {
      setDepsDialog({
        ...depsDialog,
        phase: 'error',
        error: e.message || t('component.loadFailed'),
      });
      await loadLocal();
    }
  };

  const handleUpload = async (file: File) => {
    setBusy('upload');
    setError('');
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch(API.componentUpload, { method: 'POST', body: form });
      const data = await res.json().catch(() => ({} as any));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      if (data.success === false) throw new Error(data.error || t('component.uploadFailed'));
      flash(data.message || t('component.operationSuccess'));
      await loadLocal();
      handleDepsResult(data);
    } catch (e: any) {
      setError(e.message || t('component.uploadFailed'));
    } finally {
      setBusy('');
    }
  };

  const handleUnload = (name: string) => setUnloadTarget(name);

  const doUnload = async (name: string) => {
    setBusy(`unload:${name}`);
    setError('');
    try {
      const data = await fetchJSON<any>(API.componentUnload(name), { method: 'DELETE' });
      if (data.status !== 'ok') throw new Error(data.message || t('component.unloadFailed'));
      await loadLocal();
      // 市场账本已清，重新拉一次才能刷掉「已安装」标记
      searchMarket(1);
      flash(data.message || t('component.operationSuccess'));
    } catch (e: any) {
      setError(e.message || t('component.unloadFailed'));
    } finally {
      setBusy('');
    }
  };

  const handleInstall = async (slug: string) => {
    setInstalling(slug);
    setError('');
    try {
      const data = await postJSON<any>(API.componentMarketInstall, { slug });
      if (data.success === false) throw new Error(data.error || t('component.installFailed'));
      flash(data.message || t('component.operationSuccess'));
      await Promise.all([loadLocal(), searchMarket(1)]);
      handleDepsResult(data);
    } catch (e: any) {
      setError(e.message || t('component.installFailed'));
    } finally {
      setInstalling('');
    }
  };

  const handleDetail = async (slug: string) => {
    try {
      const data = await fetchJSON<any>(API.componentMarketInfo(slug));
      if (data.success === false) throw new Error(data.error || t('component.installFailed'));
      setDetailClosing(false);
      setDetail(data);
    } catch (e: any) {
      setError(e.message || t('component.installFailed'));
    }
  };

  const closeDetail = () => {
    setDetailClosing(true);
    setTimeout(() => {
      setDetail(null);
      setDetailClosing(false);
    }, 150);
  };

  const disabled = !!busy || !!installing;
  const categoryItems = CATEGORIES.map((c) => ({ value: c, label: c || t('component.allCategories') }));

  return (
    <div className="settings-panel">
      <div className="skill-header">
        <h3>{t('settings.tabs.component')}</h3>
        <div className="skill-header-actions">
          <button className="btn-secondary btn-sm" onClick={handleReload} disabled={disabled} title={t('component.reload')}>
            <Icons.Refresh size={14} />
          </button>
          <button className="btn-primary btn-sm" onClick={() => fileRef.current?.click()} disabled={disabled} title={t('component.addHint')}>
            <Icons.Plus size={14} />
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".py,.zip,.tar,.gz,.tgz,.bz2,.xz"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) handleUpload(file);
            }}
          />
        </div>
      </div>

      {error && <div className="memory-feedback error">{error}</div>}
      {notice && <div className="memory-feedback success">{notice}</div>}

      <div className="component-toolbar">
        <div className="scheduler-search">
          <Icons.Search size={14} />
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t('component.filterPlaceholder')}
          />
        </div>
      </div>

      <div className="settings-section">
        <h4>{t('component.loaded')} ({components.length})</h4>
        {loading ? (
          <div className="loading-indicator">{t('common.loading')}</div>
        ) : filtered.length === 0 ? (
          <div className="memory-empty">{t('component.noLoaded')}</div>
        ) : (
          <div className="component-grid">
            {filtered.map((c) => (
              <div key={c.cell_name} className="component-card">
                <div className="component-card-head">
                  <span className="component-card-title" title={c.cell_name}>{c.cell_name}</span>
                  {c.is_builtin && (
                    <span className="memory-status-pill category" title={t('component.builtinHint')}>
                      {t('component.builtin')}
                    </span>
                  )}
                </div>
                <div className="component-card-meta">
                  <span>{t('component.commandCount', { count: c.command_count })}</span>
                </div>
                <div className="component-card-desc" title={c.source_file || Object.keys(c.commands || {}).join(' · ')}>
                  {c.source_file ? c.source_file.replace(/\\/g, '/').split('/').slice(-2).join('/') : Object.keys(c.commands || {}).join(' · ')}
                </div>
                <div className="component-card-actions">
                  {!c.is_builtin && (
                    <button
                      className="btn-link"
                      style={{ color: 'var(--accent-danger)' }}
                      disabled={disabled}
                      title={t('component.unloadHint')}
                      onClick={() => handleUnload(c.cell_name)}
                    >
                      {busy === `unload:${c.cell_name}` ? t('component.unloading') : t('component.unload')}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="settings-section">
        <h4>{t('component.available')} ({discovered.length})</h4>
        {discovered.length === 0 ? (
          <div className="memory-empty">{t('component.noAvailable')}</div>
        ) : (
          <div className="component-grid">
            {discovered.map((d) => (
              <div key={d.module_path} className="component-card">
                <div className="component-card-head">
                  <span className="component-card-title" title={d.class_name}>{d.class_name}</span>
                  {d.is_new && <span className="memory-status-pill category">new</span>}
                </div>
                <div className="component-card-desc" title={d.file}>{d.file}</div>
                <div className="component-card-actions">
                  <button className="btn-link" disabled={disabled} onClick={() => handleLoad(d.class_name)}>
                    {busy === `load:${d.class_name}` ? t('common.loading') : t('component.load')}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="settings-section">
        <h4>
          {t('component.market')}
          <span className="memory-status-pill category" style={{ marginLeft: 8 }}>{t('component.marketHint')}</span>
        </h4>
        <div className="component-toolbar">
          <div className="scheduler-search">
            <Icons.Search size={14} />
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') searchMarket(1); }}
              placeholder={t('component.searchPlaceholder')}
            />
          </div>
          <CustomDropdown value={category} items={categoryItems} onChange={setCategory} />
          <button className="btn-secondary btn-sm" onClick={() => searchMarket(1)} disabled={marketLoading}>
            {marketLoading ? <Icons.Loader size={14} /> : <Icons.Search size={14} />}
          </button>
        </div>

        {marketError && <div className="memory-feedback error">{marketError}</div>}

        {marketLoading ? (
          <div className="loading-indicator">{t('common.loading')}</div>
        ) : marketItems.length === 0 ? (
          <div className="memory-empty">{t('component.noResults')}</div>
        ) : (
          <div className="component-grid">
            {marketItems.map((m) => (
              <div key={m.slug} className="component-card">
                <div className="component-card-head">
                  <span className="component-card-title" title={m.slug}>{m.name || m.slug}</span>
                  {m.installed && <span className="memory-status-pill">{t('component.installed')}</span>}
                </div>
                <div className="component-card-meta">
                  <span className="memory-status-pill category">{m.slug}</span>
                  {m.category && <span className="memory-status-pill category">{m.category}</span>}
                  {m.latest_version && <span>v{m.latest_version}</span>}
                </div>
                <div className="component-card-desc" title={m.summary}>{m.summary}</div>
                <div className="component-card-actions">
                  <button className="btn-link" onClick={() => handleDetail(m.slug)}>{t('component.viewDetail')}</button>
                  <button className="btn-link" disabled={disabled} onClick={() => handleInstall(m.slug)}>
                    {installing === m.slug ? t('component.installing') : t('component.install')}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        {marketTotal > marketItems.length && (
          <div className="component-card-meta" style={{ marginTop: 8 }}>
            {marketItems.length} / {marketTotal}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!unloadTarget}
        title={t('component.unload')}
        danger
        confirmLabel={t('component.unload')}
        cancelLabel={t('common.cancel')}
        message={
          <div style={{ whiteSpace: 'pre-line' }}>
            {t('component.confirmUnload', { name: unloadTarget })}
          </div>
        }
        onConfirm={() => {
          const name = unloadTarget;
          setUnloadTarget('');
          doUnload(name);
        }}
        onCancel={() => setUnloadTarget('')}
      />

      <ConfirmDialog
        open={!!depsDialog}
        title={t('component.depsTitle')}
        danger={depsDialog?.phase === 'error'}
        confirmLabel={
          depsDialog?.phase === 'error' ? t('component.close') : t('component.depsInstallNow')
        }
        cancelLabel={t('common.cancel')}
        busy={depsDialog?.phase === 'installing' || depsDialog?.phase === 'reloading'}
        busyLabel={
          depsDialog?.phase === 'installing'
            ? t('component.depsInstalling')
            : t('component.depsReloading')
        }
        onConfirm={depsDialog?.phase === 'error' ? () => setDepsDialog(null) : runDepsInstall}
        onCancel={() => setDepsDialog(null)}
        message={
          <div style={{ whiteSpace: 'pre-line' }}>
            {depsDialog?.phase === 'error'
              ? `${t('component.depsInstallFailed')}\n${depsDialog.error || ''}`
              : depsDialog?.phase === 'confirm'
                ? t('component.depsConfirmList', { list: depsDialog.specs.join(' ') })
                : depsDialog?.phase === 'installing'
                  ? t('component.depsInstalling')
                  : t('component.depsReloading')}
          </div>
        }
      />

      {detail && createPortal(
        <div className={`skill-detail-modal ${detailClosing ? 'closing' : ''}`}>
          <div className="modal-overlay" onClick={closeDetail} />
          <div className="modal-content">
            <div className="modal-header">
              <h3>{detail.name || detail.slug}</h3>
              <button className="btn-close" onClick={closeDetail}><Icons.Close /></button>
            </div>
            <div className="modal-body">
              <div className="detail-row"><label>{t('component.summary')}</label><span>{detail.summary || '-'}</span></div>
              <div className="detail-row"><label>{t('component.author')}</label><span>{detail.author || '-'}</span></div>
              <div className="detail-row"><label>{t('component.updatedAt')}</label><span>{detail.updated_at || '-'}</span></div>
              <div className="detail-row"><label>slug</label><span>{detail.slug}</span></div>
              <div className="detail-row">
                <label>version</label>
                <span>{detail.resolved_version || detail.latest_version || '-'}</span>
              </div>
              <div className="detail-row">
                <label>sha256</label>
                <span className="file-path">{detail.checks?.sha256 || '-'}</span>
              </div>
              {detail.readme && (
                <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{detail.readme}</pre>
              )}
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};
