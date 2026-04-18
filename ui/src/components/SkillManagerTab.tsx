import React, { useState, useEffect, useCallback, useMemo } from 'react';
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
  const [installPath, setInstallPath] = useState('');
  const [showInstallForm, setShowInstallForm] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<SkillInfo | null>(null);
  const [showDetail, setShowDetail] = useState(false);

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

  const handleSelectFolder = async () => {
    try {
      const data = await fetchJSON<any>(API.skillSelectFolder);
      if (data.success && data.path) {
        setInstallPath(data.path);
        setShowInstallForm(true);
      }
    } catch (e: any) {
      console.error('Failed to select folder:', e);
    }
  };

  const handleInstall = async () => {
    if (!installPath.trim()) return;
    await doSave(async () => {
      await postJSON<any>(API.skillInstall, {
        source_dir: installPath.trim(),
        source: 'local',
      });
      setInstallPath('');
      setShowInstallForm(false);
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
      setShowDetail(true);
    } catch (e: any) {
      console.error('Failed to load skill detail:', e);
    }
  };

  return (
    <div className="settings-panel">
      <div className="skill-header">
        <h3>{t('settings.tabs.skill')}</h3>
        <div className="skill-header-actions">
          <button className="btn-secondary btn-sm" onClick={handleRefresh} disabled={saving} title={t('skill.refresh')}>
            <Icons.Refresh size={14} />
          </button>
          <button className="btn-primary btn-sm" onClick={handleSelectFolder} title={t('skill.install')}>
            <Icons.Plus size={14} />
          </button>
        </div>
      </div>

      {error && <div className="memory-feedback error">{error}</div>}
      {saved && <div className="memory-feedback success">{t('skill.operationSuccess')}</div>}

      {showInstallForm && (
        <div className="model-card">
          <div className="model-card-header">
            <h4>{t('skill.installFromPath')}</h4>
          </div>
          <div className="model-card-body">
            <div className="form-group">
              <label>{t('skill.directoryPath')}</label>
              <div className="skill-path-input">
                <input
                  type="text"
                  value={installPath}
                  onChange={(e) => setInstallPath(e.target.value)}
                  placeholder="D:/skills/my-skill"
                />
                <button className="btn-secondary btn-sm" onClick={handleSelectFolder}>
                  {t('skill.browse')}
                </button>
              </div>
            </div>
            <div className="form-actions">
              <button className="btn-primary" onClick={handleInstall} disabled={saving || !installPath.trim()}>
                {saving ? t('common.loading') : t('skill.install')}
              </button>
              <button className="btn-secondary" onClick={() => { setShowInstallForm(false); setInstallPath(''); }}>
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
        <h4>{t('skill.installedSkills')} ({filteredSkills.length}/{skills.length})</h4>
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

      {showDetail && selectedSkill && (
        <div className="skill-detail-modal">
          <div className="modal-overlay" onClick={() => setShowDetail(false)} />
          <div className="modal-content">
            <div className="modal-header">
              <h3>{selectedSkill.name}</h3>
              <button className="btn-close" onClick={() => setShowDetail(false)}>
                <Icons.Close />
              </button>
            </div>
            <div className="modal-body">
              <div className="detail-row">
                <label>{t('skill.description')}</label>
                <span>{selectedSkill.frontmatter?.description || selectedSkill.description || '-'}</span>
              </div>
              <div className="detail-row">
                <label>{t('skill.category')}</label>
                <span>{selectedSkill.frontmatter?.category || '-'}</span>
              </div>
              <div className="detail-row">
                <label>{t('skill.version')}</label>
                <span>{selectedSkill.frontmatter?.version || '-'}</span>
              </div>
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
              {selectedSkill.frontmatter && Object.keys(selectedSkill.frontmatter).length > 0 && (
                <div className="detail-row">
                  <label>{t('skill.frontmatter')}</label>
                  <pre>{JSON.stringify(selectedSkill.frontmatter, null, 2)}</pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
