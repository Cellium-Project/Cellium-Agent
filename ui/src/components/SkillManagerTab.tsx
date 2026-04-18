import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { API, fetchJSON, postJSON } from '../utils/api';
import { Icons } from './Icons';

interface SkillInfo {
  name: string;
  description: string;
  skill_md_path: string;
  frontmatter?: Record<string, any>;
  source?: string;
  installed_at?: string;
  updated_at?: string;
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

export const SkillManagerTab: React.FC = () => {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterQuery, setFilterQuery] = useState('');
  const [selectedArchive, setSelectedArchive] = useState<File | null>(null);
  const [skillFolderName, setSkillFolderName] = useState('');
  const [showInstallForm, setShowInstallForm] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<SkillInfo | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const archiveInputRef = React.useRef<HTMLInputElement>(null);

  const { saving, saved, error, doSave } = useSavingState();

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchJSON<any>(API.skills);
      setSkills(data.skills || []);
    } catch (e: any) {
      console.error('Failed to load skills:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  const filteredSkills = useMemo(() => {
    if (!filterQuery.trim()) return skills;
    const query = filterQuery.toLowerCase();
    return skills.filter(skill =>
      skill.name.toLowerCase().includes(query) ||
      (skill.description || '').toLowerCase().includes(query) ||
      (skill.frontmatter?.category || '').toLowerCase().includes(query)
    );
  }, [skills, filterQuery]);

  const handleSelectArchive = () => {
    archiveInputRef.current?.click();
  };

  const handleArchiveChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const validExtensions = ['.zip', '.tar', '.tar.gz', '.tgz'];
      const fileName = file.name.toLowerCase();
      const isValid = validExtensions.some(ext => fileName.endsWith(ext));
      
      if (!isValid) {
        alert('请选择 .zip 或 .tar.gz 格式的压缩包');
        return;
      }
      
      const archiveName = file.name.replace(/\.(zip|tar\.gz|tgz|tar)$/i, '');
      setSkillFolderName(archiveName);
      setSelectedArchive(file);
      setShowInstallForm(true);
    }
  };

  const handleInstall = async () => {
    if (!selectedArchive) return;
    await doSave(async () => {
      const formData = new FormData();
      formData.append('archive', selectedArchive);
      
      const response = await fetch(API.skillInstall, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: '安装失败' }));
        throw new Error(error.detail || `HTTP ${response.status}`);
      }

      setSelectedArchive(null);
      setSkillFolderName('');
      setShowInstallForm(false);
      if (archiveInputRef.current) {
        archiveInputRef.current.value = '';
      }
      await loadSkills();
    });
  };

  const handleUninstall = async (name: string) => {
    if (!confirm(`确定要卸载 Skill "${name}" 吗？`)) return;
    await doSave(async () => {
      await fetchJSON<any>(API.skillDetail(name), { method: 'DELETE' });
      if (selectedSkill?.name === name) {
        setSelectedSkill(null);
        setShowDetail(false);
      }
      await loadSkills();
    });
  };

  const handleRefresh = async () => {
    await doSave(async () => {
      await postJSON<any>(API.skillRefreshIndex, {});
      await loadSkills();
    });
  };

  const handleViewDetail = async (skill: SkillInfo) => {
    try {
      const data = await fetchJSON<any>(API.skillDetail(skill.name));
      setSelectedSkill(data);
      setIsClosing(false);
      setShowDetail(true);
    } catch (e: any) {
      console.error('Failed to load skill detail:', e);
    }
  };

  const handleCloseDetail = () => {
    setIsClosing(true);
    setTimeout(() => {
      setShowDetail(false);
      setIsClosing(false);
    }, 150);
  };

  return (
    <div className="settings-panel">
      <div className="skill-header">
        <h3>{t('settings.tabs.skill')}</h3>
        <div className="skill-header-actions">
          <button className="btn-secondary btn-sm" onClick={handleRefresh} disabled={saving} title={t('skill.refresh')}>
            <Icons.Refresh size={14} />
          </button>
          <button className="btn-primary btn-sm" onClick={handleSelectArchive} title={t('skill.install')}>
            <Icons.Plus size={14} />
          </button>
        </div>
      </div>

      <input
        ref={archiveInputRef}
        type="file"
        accept=".zip,.tar,.tar.gz,.tgz"
        style={{ display: 'none' }}
        onChange={handleArchiveChange}
      />

      {error && <div className="memory-feedback error">{error}</div>}
      {saved && <div className="memory-feedback success">{t('skill.operationSuccess')}</div>}

      {showInstallForm && selectedArchive && (
        <div className="model-card">
          <div className="model-card-header">
            <h4>{t('skill.installFromArchive')}</h4>
          </div>
          <div className="model-card-body">
            <div className="form-group">
              <label>{t('skill.selectedArchive')}</label>
              <div className="selected-file-info">
                <span className="file-name">{skillFolderName}</span>
                <span className="file-size">({(selectedArchive.size / 1024).toFixed(2)} KB)</span>
              </div>
            </div>
            <div className="form-actions">
              <button className="btn-primary" onClick={handleInstall} disabled={saving}>
                {saving ? t('common.loading') : t('skill.install')}
              </button>
              <button className="btn-secondary" onClick={() => { 
                setShowInstallForm(false); 
                setSelectedArchive(null);
                setSkillFolderName('');
                if (archiveInputRef.current) {
                  archiveInputRef.current.value = '';
                }
              }}>
                {t('common.cancel')}
              </button>
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
            placeholder={t('skill.filterPlaceholder')}
          />
        </div>
      </div>

      <div className="settings-section">
        <h4>{t('skill.installedSkills')} ({skills.length})</h4>
        {loading ? (
          <div className="loading-indicator">{t('common.loading')}</div>
        ) : filteredSkills.length === 0 ? (
          <div className="memory-empty">{filterQuery ? t('skill.noMatch') : t('skill.noSkills')}</div>
        ) : (
          filteredSkills.map((skill) => (
            <div key={skill.name} className="model-card">
              <div className="model-card-header">
                <div className="skill-info">
                  <span className="skill-name">{skill.name}</span>
                  {skill.frontmatter?.category && (
                    <span className="memory-status-pill category">{skill.frontmatter.category}</span>
                  )}
                </div>
                <div className="skill-actions">
                  <button className="btn-link" onClick={() => handleViewDetail(skill)}>
                    {t('skill.viewDetail')}
                  </button>
                  <button className="btn-link" style={{ color: 'var(--accent-danger)' }} onClick={() => handleUninstall(skill.name)}>
                    {t('skill.uninstall')}
                  </button>
                </div>
              </div>
              <div className="model-card-body">
                <div className="skill-desc">{skill.description || skill.frontmatter?.description || ''}</div>
              </div>
            </div>
          ))
        )}
      </div>

      {showDetail && selectedSkill && createPortal(
        <div className={`skill-detail-modal ${isClosing ? 'closing' : ''}`}>
          <div className="modal-overlay" onClick={handleCloseDetail} />
          <div className="modal-content">
            <div className="modal-header">
              <h3>{selectedSkill.name}</h3>
              <button className="btn-close" onClick={handleCloseDetail}>
                <Icons.Close />
              </button>
            </div>
            <div className="modal-body">
              {selectedSkill.frontmatter && Object.keys(selectedSkill.frontmatter).length > 0 ? (
                Object.entries(selectedSkill.frontmatter).map(([key, value]) => {
                  let displayValue: React.ReactNode = '-';
                  if (value === null || value === undefined) {
                    displayValue = '-';
                  } else if (Array.isArray(value)) {
                    displayValue = value.length > 0 ? value.join(', ') : '-';
                  } else if (typeof value === 'object') {
                    const objKeys = Object.keys(value as object);
                    displayValue = objKeys.length > 0 
                      ? <pre style={{margin: 0, whiteSpace: 'pre-wrap', fontSize: '12px'}}>{JSON.stringify(value, null, 2)}</pre>
                      : '-';
                  } else if (typeof value === 'boolean') {
                    displayValue = value ? '是' : '否';
                  } else {
                    displayValue = String(value);
                  }
                  return (
                    <div key={key} className="detail-row">
                      <label>{key}</label>
                      <span>{displayValue}</span>
                    </div>
                  );
                })
              ) : (
                selectedSkill.description && (
                  <div className="detail-row">
                    <label>{t('skill.description')}</label>
                    <span>{selectedSkill.description}</span>
                  </div>
                )
              )}
              <div className="detail-row">
                <label>{t('skill.source')}</label>
                <span>{selectedSkill.source || '-'}</span>
              </div>
              <div className="detail-row">
                <label>{t('skill.installedAt')}</label>
                <span>{selectedSkill.installed_at ? new Date(selectedSkill.installed_at).toLocaleString() : '-'}</span>
              </div>
              <div className="detail-row">
                <label>{t('skill.filePath')}</label>
                <span className="file-path">{selectedSkill.skill_md_path}</span>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};
