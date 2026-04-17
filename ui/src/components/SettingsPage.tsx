import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppStore } from '../stores/appStore';
import { API, fetchJSON, postJSON, putJSON } from '../utils/api';
import { Icons } from './Icons';
import { CustomDropdown } from './CustomDropdown';
import { MemoryManagerPanel } from './MemoryManagerPanel';
import { Collapsible } from './Collapsible';
import type { Theme, Language } from '../stores/appStore';


// ── 设置 Tab 定义 ───────────────────────────────────────────
const useSettingsTabs = () => {
  const { t } = useTranslation();
  return [
    { id: 'model', label: t('settings.tabs.model'), Icon: Icons.Database },
    { id: 'agent', label: t('settings.tabs.agent'), Icon: Icons.Bot },
    { id: 'memory', label: t('settings.tabs.memory'), Icon: Icons.Brain },
    { id: 'learning', label: t('settings.tabs.learning'), Icon: Icons.BookOpen },
    { id: 'heuristics', label: t('settings.tabs.heuristics'), Icon: Icons.Lightbulb },
    { id: 'security', label: t('settings.tabs.security'), Icon: Icons.Shield },
    { id: 'channel', label: t('settings.tabs.channel'), Icon: Icons.Globe },
    { id: 'logging', label: t('settings.tabs.logging'), Icon: Icons.FileText },
    { id: 'appearance', label: t('settings.tabs.appearance'), Icon: Icons.Palette },
  ] as const;
};

// ── 通用组件：字段标签 ─────────────────────────────────────
const FieldLabel: React.FC<{ label: string; desc?: string }> = ({ label, desc }) => (
  <div className="field-label-wrapper">
    <span className="settings-field-label">{label}</span>
    {desc && <span className="field-desc">{desc}</span>}
  </div>
);

// 通用组件：保存按钮反馈
function useSavingState() {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const doSave = useCallback(async (saveFn: () => Promise<any>) => {
    setSaving(true);
    try {
      await saveFn();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }, []);

  return { saving, saved, doSave };
}

// ═════════════════════════════════════════════════════════════
// 模型配置 Tab
// ═════════════════════════════════════════════════════════════
const ModelSettings: React.FC = () => {
  const { t } = useTranslation();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const { saving, saved, doSave } = useSavingState();

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('llm')).then(data => {
      // 兼容旧配置，转换为 models 格式
      if (data && !data.models && data.openai) {
        data.models = [{
          name: data.openai.model || 'default',
          api_key: data.openai.api_key || '',
          base_url: data.openai.base_url || '',
          model: data.openai.model || '',
          temperature: data.openai.temperature || 0.7,
          timeout: data.openai.timeout || 120,
        }];
        data.current_model = data.openai.model || 'default';
      }
      setConfig(data || {}); 
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const updateField = (path: string, value: any) => {
    setConfig(prev => {
      const keys = path.split('.');
      const next = { ...prev };
      let cur = next;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!cur[keys[i]]) cur[keys[i]] = {};
        cur[keys[i]] = { ...cur[keys[i]] };
        cur = cur[keys[i]];
      }
      cur[keys[keys.length - 1]] = value;
      return next;
    });
  };

  const updateModelField = (index: number, field: string, value: any) => {
    setConfig(prev => {
      const models = [...(prev.models || [])];
      models[index] = { ...models[index], [field]: value };
      return { ...prev, models };
    });
  };

  const addModel = () => {
    setConfig(prev => ({
      ...prev,
      models: [...(prev.models || []), {
        name: `model-${(prev.models?.length || 0) + 1}`,
        api_key: '',
        base_url: 'https://api.openai.com/v1',
        model: '',
        temperature: 0.7,
        timeout: 120,
      }],
    }));
  };

  const removeModel = (index: number) => {
    setConfig(prev => {
      const newModels = (prev.models || []).filter((_: any, i: number) => i !== index);
      let newCurrentModel = prev.current_model;
      if (newModels.length === 0) {
        newCurrentModel = '';
      }
      return {
        ...prev,
        models: newModels,
        current_model: newCurrentModel,
      };
    });
  };

  const handleSave = () => doSave(async () => {
    const saveConfig = { ...config };
    if (!saveConfig.current_model && saveConfig.models?.length > 0) {
      saveConfig.current_model = saveConfig.models[0].name;
    }
    const currentModel = saveConfig.models?.find((m: any) => m.name === saveConfig.current_model);
    if (currentModel) {
      saveConfig.openai = {
        api_key: currentModel.api_key,
        base_url: currentModel.base_url,
        model: currentModel.model,
        temperature: currentModel.temperature,
        timeout: currentModel.timeout,
      };
    } else {
      delete saveConfig.openai;
    }
    await putJSON(API.configUpdate('llm'), { value: saveConfig, persist: true });
    await postJSON(API.modelReloadEngine, {});
  });

  const models = config.models || [];
  // 直接使用 config.current_model，确保选择后能正确更新
  const currentModelName = config.current_model || '';
  const displayModelName = currentModelName || (models[0]?.name || '');
  const streaming = config.streaming || {};

  return (
    <div className="settings-panel">
      <h3 className="settings-section-title">
        <span><Icons.Database size={18} /> {t('settings.model.title')}</span>
      </h3>
      <p className="settings-desc">{t('settings.model.description')}</p>

      {loading ? (
        <div className="settings-loading"><span className="loading-dots"><span></span><span></span><span></span></span> {t('common.loading')}</div>
      ) : (
        <>
          <div className="settings-grid">
            <div className="form-group">
              <FieldLabel label={t('settings.model.currentModel')} />
              <CustomDropdown
                value={displayModelName}
                items={models.map((m: any) => ({ value: m.name, label: `${m.name} (${m.model || t('settings.model.notSet')})` }))}
                onChange={val => updateField('current_model', val)}
              />
            </div>

            <div className="form-group">
              <FieldLabel label={t('settings.model.streaming')} />
              <label className="toggle-switch">
                <input type="checkbox" checked={!!streaming.enabled} onChange={e => updateField('streaming.enabled', e.target.checked)} />
                <span className="toggle-slider"></span>
                <span className="toggle-label">{streaming.enabled ? t('settings.model.streamingEnabled') : t('settings.model.streamingDisabled')}</span>
              </label>
            </div>
          </div>

          <div className="models-list">
            <div className="models-list-header">
              <h4>{t('settings.model.modelList')}</h4>
              <button className="btn-small" onClick={addModel}>+ {t('settings.model.addModel')}</button>
            </div>

            {models.length === 0 && (
              <p className="settings-desc">{t('settings.model.noModels')}</p>
            )}

            {models.map((model: any, index: number) => (
              <div key={index} className="model-card">
                <div className="model-card-header">
                  <input
                    type="text"
                    value={model.name}
                    onChange={e => updateModelField(index, 'name', e.target.value)}
                    placeholder={t('settings.model.namePlaceholder')}
                    className="model-name-input"
                  />
                  <button className="btn-icon" onClick={() => removeModel(index)} title={t('common.delete')}>
                    <Icons.X size={16} />
                  </button>
                </div>
                <div className="model-card-body">
                  <div className="form-group">
                    <FieldLabel label={t('settings.model.baseUrl')} />
                    <input type="text" value={model.base_url || ''} onChange={e => updateModelField(index, 'base_url', e.target.value)} placeholder="https://api.openai.com/v1" />
                  </div>
                  <div className="form-group">
                    <FieldLabel label={t('settings.model.apiKey')} />
                    <input type="password" value={model.api_key || ''} onChange={e => updateModelField(index, 'api_key', e.target.value)} placeholder="sk-..." />
                  </div>
                  <div className="form-group">
                    <FieldLabel label={t('settings.model.modelId')} />
                    <input type="text" value={model.model || ''} onChange={e => updateModelField(index, 'model', e.target.value)} placeholder="gpt-4o" />
                  </div>
                  <div className="form-row">
                    <div className="form-group">
                      <FieldLabel label={t('settings.model.temperature')} />
                      <input type="number" value={Number(model.temperature) || 0.7} min={0} max={2} step={0.1}
                        onChange={e => updateModelField(index, 'temperature', parseFloat(e.target.value))} />
                    </div>
                    <div className="form-group">
                      <FieldLabel label={t('settings.model.timeout')} />
                      <input type="number" value={Number(model.timeout) || 120} min={10} max={600}
                        onChange={e => updateModelField(index, 'timeout', parseInt(e.target.value))} />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="form-actions">
            <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
              {saving ? t('common.saving') : saved ? `✓ ${t('settings.model.saved')}` : t('settings.model.saveAndReload')}
            </button>
          </div>
        </>
      )}
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ Agent 行为 Tab
// ═════════════════════════════════════════════════════════════
const AgentSettings: React.FC = () => {
  const { t } = useTranslation();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const { saving, saved, doSave } = useSavingState();

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('agent')).then(data => {
      setConfig(data); setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const updateField = (path: string, value: any) => {
    setConfig(prev => {
      const keys = path.split('.');
      const next = { ...prev };
      let cur = next;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!cur[keys[i]]) cur[keys[i]] = {};
        cur[keys[i]] = { ...cur[keys[i]] };
        cur = cur[keys[i]];
      }
      cur[keys[keys.length - 1]] = value;
      return next;
    });
  };

  const handleSave = () => doSave(async () => {
    await putJSON(API.configUpdate('agent'), { value: config, persist: true });
  });

  return (
    <div className="settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <Icons.Bot size={16} /> {t('settings.agent.title')}
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label={t('settings.agent.iterationLimit')} desc={t('settings.agent.iterationLimitDesc')} />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!config.enforce_iteration_limit} onChange={e => updateField('enforce_iteration_limit', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{config.enforce_iteration_limit ? t('settings.agent.iterationLimitEnabled') : t('settings.agent.iterationLimitDisabled')}</span>
            </label>
          </div>
          {config.enforce_iteration_limit && (
            <div className="form-group">
              <FieldLabel label={t('settings.agent.maxIterations')} desc={t('settings.agent.maxIterationsDesc')} />
              <input type="number" value={Number(config.max_iterations) || 10} min={1} max={100}
                onChange={e => updateField('max_iterations', parseInt(e.target.value))} />
            </div>
          )}
          <div className="form-group">
            <FieldLabel label={t('settings.agent.requestTimeout')} />
            <input type="number" value={Number(config.request_timeout) || 300} min={30} max={3600}
              onChange={e => updateField('request_timeout', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.agent.flashMode')} desc={t('settings.agent.flashModeDesc')} />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!config.flash_mode} onChange={e => updateField('flash_mode', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{config.flash_mode ? t('settings.agent.flashModeEnabled') : t('settings.agent.flashModeDisabled')}</span>
            </label>
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.agent.shellWorkDir')} desc={t('settings.agent.shellWorkDirDesc')} />
            <input type="text" value={config.shell_cwd || ''} placeholder="如: D:\\projects 或 /home/user/projects"
              onChange={e => updateField('shell_cwd', e.target.value)} />
          </div>
        </div>
        <div className="form-actions">
          <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
            {saving ? t('common.saving') : saved ? `✓ ${t('common.saved')}` : t('settings.agent.save')}
          </button>
        </div>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ 安全策略 Tab
// ═════════════════════════════════════════════════════════════
const SecuritySettings: React.FC = () => {
  const { t } = useTranslation();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [newBlacklistItem, setNewBlacklistItem] = useState('');
  const { saving, saved, doSave } = useSavingState();

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('security')).then(data => {
      setConfig(data); setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const updateField = (path: string, value: any) => {
    setConfig(prev => {
      const keys = path.split('.');
      const next = { ...prev };
      let cur = next;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!cur[keys[i]]) cur[keys[i]] = {};
        cur[keys[i]] = { ...cur[keys[i]] };
        cur = cur[keys[i]];
      }
      cur[keys[keys.length - 1]] = value;
      return next;
    });
  };

  const addBlacklistItem = () => {
    const item = newBlacklistItem.trim();
    if (!item) return;
    const list = [...(config.command_blacklist || []), item];
    updateField('command_blacklist', list);
    setNewBlacklistItem('');
  };

  const removeBlacklistItem = (idx: number) => {
    const list = [...(config.command_blacklist || [])];
    list.splice(idx, 1);
    updateField('command_blacklist', list);
  };

  const handleSave = () => doSave(async () => {
    await putJSON(API.configUpdate('security'), { value: config, persist: true });
  });

  return (
    <div className="settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <Icons.Shield size={16} /> {t('settings.security.title')}
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label={t('settings.security.permissionLevel')} />
            <CustomDropdown
              value={config.permission_level || 'standard'}
              items={[
                { value: 'read_only', label: t('settings.security.readOnly') },
                { value: 'standard', label: t('settings.security.standard') },
                { value: 'admin', label: t('settings.security.admin') },
                { value: 'unrestricted', label: t('settings.security.unrestricted') },
              ]}
              onChange={val => updateField('permission_level', val)}
            />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.security.commandTimeout')} />
            <input type="number" value={Number(config.command_timeout) || 30} min={5} max={300}
              onChange={e => updateField('command_timeout', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.security.shellHardTimeout')} />
            <input type="number" value={Number(config.shell_hard_timeout) || 60} min={10} max={600}
              onChange={e => updateField('shell_hard_timeout', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.security.outputTruncate')} />
            <input type="number" value={Number(config.max_output_bytes) || 8192} min={1024} max={1048576}
              onChange={e => updateField('max_output_bytes', parseInt(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            {t('settings.security.commandBlacklist')}
          </div>
        </div>
        <div className="tag-list-editor">
          {(config.command_blacklist || []).map((item: string, idx: number) => (
            <span key={idx} className="tag-item">
              <code>{item}</code>
              <button className="tag-remove" onClick={() => removeBlacklistItem(idx)}>×</button>
            </span>
          ))}
          <div className="tag-input-row">
            <input type="text" value={newBlacklistItem} onChange={e => setNewBlacklistItem(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addBlacklistItem())}
              placeholder={t('settings.security.blacklistPlaceholder')} />
            <button className="btn-secondary btn-sm" onClick={addBlacklistItem}>{t('settings.security.add')}</button>
          </div>
        </div>
      </div>

      <div className="form-actions">
        <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
          {saving ? t('common.saving') : saved ? `✓ ${t('common.saved')}` : t('settings.security.save')}
        </button>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// QQ 通道配置子组件
// ═════════════════════════════════════════════════════════════
const QQChannelCard: React.FC<{
  config: Record<string, any>;
  onChange: (config: Record<string, any>) => void;
  saving: boolean;
  saved: boolean;
  onSave: () => void;
  error: string | null;
}> = ({ config, onChange, saving, saved, onSave, error }) => {
  const { t } = useTranslation();
  const updateField = (field: string, value: any) => {
    onChange({ ...config, [field]: value });
  };

  return (
    <div className="settings-card" style={{ marginBottom: 24 }}>
      <div className="settings-card-header">
        <div className="settings-card-title">
          <Icons.Globe size={16} /> {t('settings.channel.qq.title')}
        </div>
        <button className="btn-primary btn-sm" onClick={onSave} disabled={saving}>
          {saving ? t('common.saving') : saved ? `✓ ${t('common.saved')}` : t('settings.channel.telegram.save')}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="settings-card-grid">
        <div className="form-group">
          <FieldLabel label={t('settings.channel.qq.enabled')} />
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={config.enabled !== false}
              onChange={e => updateField('enabled', e.target.checked)}
            />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.enabled !== false ? t('settings.channel.qq.enabledOn') : t('settings.channel.qq.enabledOff')}</span>
          </label>
        </div>

        <div className="form-group">
          <FieldLabel label={t('settings.channel.qq.autoStart')} />
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={config.auto_start !== false}
              onChange={e => updateField('auto_start', e.target.checked)}
            />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.auto_start !== false ? t('settings.channel.qq.autoStartOn') : t('settings.channel.qq.autoStartOff')}</span>
          </label>
        </div>

        <div className="form-group">
          <FieldLabel label={t('settings.channel.qq.appId')} />
          <input
            type="text"
            value={config.app_id || ''}
            onChange={e => updateField('app_id', e.target.value)}
            placeholder={t('settings.channel.qq.appIdPlaceholder')}
          />
        </div>

        <div className="form-group">
          <FieldLabel label={t('settings.channel.qq.appSecret')} />
          <input
            type="password"
            value={config.app_secret || ''}
            onChange={e => updateField('app_secret', e.target.value)}
            placeholder={t('settings.channel.qq.appSecretPlaceholder')}
          />
        </div>

        <div className="form-group">
          <FieldLabel label={t('settings.channel.qq.intents')} />
          <input
            type="number"
            value={config.intents || 1107296256}
            onChange={e => updateField('intents', parseInt(e.target.value))}
          />
        </div>

        <div className="form-group">
          <FieldLabel label={t('settings.channel.qq.credentialStatus')} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: config.app_id && config.app_secret ? 'var(--status-success-bright)' : 'var(--status-danger-bright)',
            }} />
            <span style={{ color: 'var(--text-code)', fontSize: 13 }}>
              {config.app_id && config.app_secret ? t('settings.channel.qq.credentialConfigured') : t('settings.channel.qq.credentialMissing')}
            </span>
          </div>
        </div>
      </div>

      <div className="settings-card-footer">
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>
          {t('settings.channel.qq.tip')}
        </p>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// Telegram 通道配置子组件
// ═════════════════════════════════════════════════════════════
const TelegramChannelCard: React.FC<{
  config: Record<string, any>;
  onChange: (config: Record<string, any>) => void;
  saving: boolean;
  saved: boolean;
  onSave: () => void;
  error: string | null;
}> = ({ config, onChange, saving, saved, onSave, error }) => {
  const { t } = useTranslation();
  const updateField = (field: string, value: any) => {
    onChange({ ...config, [field]: value });
  };

  const updateListField = (field: string, value: string) => {
    const list = value.split(',').map(s => s.trim()).filter(Boolean);
    onChange({ ...config, [field]: list });
  };

  return (
    <div className="settings-card">
      <div className="settings-card-header">
        <div className="settings-card-title">
          <Icons.Globe size={16} /> {t('settings.channel.telegram.title')}
        </div>
        <button className="btn-primary btn-sm" onClick={onSave} disabled={saving}>
          {saving ? t('common.saving') : saved ? `✓ ${t('common.saved')}` : t('settings.channel.telegram.save')}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="settings-card-grid">
        <div className="form-group">
          <FieldLabel label={t('settings.channel.telegram.enabled')} />
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={config.enabled === true}
              onChange={e => updateField('enabled', e.target.checked)}
            />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.enabled === true ? t('settings.channel.telegram.enabledOn') : t('settings.channel.telegram.enabledOff')}</span>
          </label>
        </div>

        <div className="form-group">
          <FieldLabel label={t('settings.channel.telegram.autoStart')} />
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={config.auto_start !== false}
              onChange={e => updateField('auto_start', e.target.checked)}
            />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.auto_start !== false ? t('settings.channel.telegram.autoStartOn') : t('settings.channel.telegram.autoStartOff')}</span>
          </label>
        </div>

        <div className="form-group" style={{ gridColumn: 'span 2' }}>
          <FieldLabel label={t('settings.channel.telegram.botToken')} desc={t('settings.channel.telegram.botTokenDesc')} />
          <input
            type="password"
            value={config.bot_token || ''}
            onChange={e => updateField('bot_token', e.target.value)}
            placeholder={t('settings.channel.telegram.botTokenPlaceholder')}
          />
        </div>

        <div className="form-group">
          <FieldLabel label={t('settings.channel.telegram.whitelistUserIds')} desc={t('settings.channel.telegram.whitelistUserIdsDesc')} />
          <input
            type="text"
            value={(config.whitelist_user_ids || []).join(', ')}
            onChange={e => updateListField('whitelist_user_ids', e.target.value)}
            placeholder={t('settings.channel.telegram.whitelistUserIdsPlaceholder')}
          />
        </div>

        <div className="form-group">
          <FieldLabel label={t('settings.channel.telegram.whitelistUsernames')} desc={t('settings.channel.telegram.whitelistUsernamesDesc')} />
          <input
            type="text"
            value={(config.whitelist_usernames || []).join(', ')}
            onChange={e => updateListField('whitelist_usernames', e.target.value)}
            placeholder={t('settings.channel.telegram.whitelistUsernamesPlaceholder')}
          />
        </div>

        <div className="form-group">
          <FieldLabel label={t('settings.channel.telegram.credentialStatus')} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: config.bot_token ? 'var(--status-success-bright)' : 'var(--status-danger-bright)',
            }} />
            <span style={{ color: 'var(--text-code)', fontSize: 13 }}>
              {config.bot_token ? t('settings.channel.telegram.credentialConfigured') : t('settings.channel.telegram.credentialMissing')}
            </span>
          </div>
        </div>
      </div>

      <div className="settings-card-footer">
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: 0 }}>
          {t('settings.channel.telegram.tip')}
        </p>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// 通道配置 Tab (多平台消息入口)
// ═════════════════════════════════════════════════════════════
const ChannelSettings: React.FC = () => {
  const { t } = useTranslation();
  const [configs, setConfigs] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string | null>>({});

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('channels')).then(data => {
      setConfigs({
        qq: data?.qq || { enabled: true, auto_start: true },
        telegram: data?.telegram || { enabled: false, auto_start: true, whitelist_user_ids: [], whitelist_usernames: [] }
      });
      setLoading(false);
    }).catch(() => {
      setConfigs({
        qq: { enabled: true, auto_start: true },
        telegram: { enabled: false, auto_start: true, whitelist_user_ids: [], whitelist_usernames: [] }
      });
      setLoading(false);
    });
  }, []);

  const handleSave = async (platform: string) => {
    setSaving(prev => ({ ...prev, [platform]: true }));
    setErrors(prev => ({ ...prev, [platform]: null }));
    try {
      const payload = { ...configs, [platform]: configs[platform] };
      await putJSON(API.configUpdate('channels'), { value: payload, persist: true });
      await fetch(`${API.channelReload}?platform=${platform}`, { method: 'POST' });
      setSaved(prev => ({ ...prev, [platform]: true }));
      setTimeout(() => setSaved(prev => ({ ...prev, [platform]: false })), 2000);
    } catch (e: any) {
      setErrors(prev => ({ ...prev, [platform]: e.message || t('common.saveFailed') }));
    } finally {
      setSaving(prev => ({ ...prev, [platform]: false }));
    }
  };

  const updateConfig = (platform: string, config: Record<string, any>) => {
    setConfigs(prev => ({ ...prev, [platform]: config }));
  };

  if (loading) {
    return <div className="settings-card"><div className="settings-loading"><span className="loading-dots"><span></span><span></span><span></span></span> {t('common.loading')}</div></div>;
  }

  return (
    <div>
      <QQChannelCard
        config={configs.qq || {}}
        onChange={(cfg) => updateConfig('qq', cfg)}
        saving={saving.qq || false}
        saved={saved.qq || false}
        onSave={() => handleSave('qq')}
        error={errors.qq || null}
      />
      <TelegramChannelCard
        config={configs.telegram || {}}
        onChange={(cfg) => updateConfig('telegram', cfg)}
        saving={saving.telegram || false}
        saved={saved.telegram || false}
        onSave={() => handleSave('telegram')}
        error={errors.telegram || null}
      />
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// 日志设置 Tab
// ═════════════════════════════════════════════════════════════
const LoggingSettings: React.FC = () => {
  const { t } = useTranslation();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const { saving, saved, doSave } = useSavingState();
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [history, setHistory] = useState<Record<string, any>[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [expandedHistory, setExpandedHistory] = useState(false);

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('logging')).then(data => {
      setConfig(data); setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const loadStatus = useCallback(() => {
    setStatusLoading(true);
    fetchJSON<Record<string, any>>(API.logsStatus)
      .then(data => { setStatus(data); setStatusLoading(false); })
      .catch(() => { setStatus(null); setStatusLoading(false); });
  }, []);

  const loadHistory = useCallback(() => {
    setHistoryLoading(true);
    fetchJSON<{ history: Record<string, any>[] }>(API.logsStatusHistory)
      .then(data => { setHistory(data.history || []); setHistoryLoading(false); })
      .catch(() => { setHistory([]); setHistoryLoading(false); });
  }, []);

  useEffect(() => {
    loadStatus();
    loadHistory();
    const timer = setInterval(() => { loadStatus(); loadHistory(); }, 3000);
    return () => clearInterval(timer);
  }, [loadStatus, loadHistory]);

  const updateField = (path: string, value: any) => {
    setConfig(prev => ({ ...prev, [path]: value }));
  };

  const handleSave = () => doSave(async () => {
    await putJSON(API.configUpdate('logging'), { value: config, persist: true });
  });

  return (
    <div className="settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <Icons.FileText size={16} /> {t('settings.logging.title')}
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label={t('settings.logging.logLevel')} />
            <CustomDropdown
              value={(config.level || 'INFO').toUpperCase()}
              items={[
                { value: 'DEBUG', label: 'DEBUG — 调试' },
                { value: 'INFO', label: 'INFO — 信息（默认）' },
                { value: 'WARNING', label: 'WARNING — 警告' },
                { value: 'ERROR', label: 'ERROR — 错误' },
              ]}
              onChange={val => updateField('level', val)}
            />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.logging.consoleOutput')} />
            <label className="toggle-switch">
              <input type="checkbox" checked={config.console !== false} onChange={e => updateField('console', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{config.console !== false ? t('settings.logging.consoleOn') : t('settings.logging.consoleOff')}</span>
            </label>
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.logging.bufferSize')} />
            <input type="number" value={Number(config.max_size) || 5000} min={100} max={100000}
              onChange={e => updateField('max_size', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.logging.logFilePath')} />
            <input type="text" value={config.file || ''} onChange={e => updateField('file', e.target.value)} placeholder={t('settings.logging.logFilePathPlaceholder')} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.logging.backupCount')} />
            <input type="number" value={Number(config.backup_count) || 5} min={0} max={20}
              onChange={e => updateField('backup_count', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.logging.logFormat')} />
            <input type="text" value={config.format || ''} onChange={e => updateField('format', e.target.value)} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <Icons.Zap size={16} /> {t('settings.logging.agentStatus')}
          </div>
          <button className="btn-secondary btn-sm" onClick={() => { loadStatus(); loadHistory(); }} disabled={statusLoading}>
            {statusLoading ? t('settings.logging.refreshing') : t('settings.logging.refresh')}
          </button>
        </div>
        <div style={{ padding: '12px 16px' }}>
          {!status || !status.available ? (
            <div style={{ color: 'var(--text-disabled)', fontSize: 13 }}>{t('settings.logging.agentNotRunning')}</div>
          ) : (
            <pre style={{
              margin: 0,
              color: 'var(--text-code)',
              fontSize: 12,
              fontFamily: 'monospace',
              background: 'var(--bg-code)',
              borderRadius: 8,
              padding: '10px 14px',
              lineHeight: 1.7,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            }}>
              {status.summary}
            </pre>
          )}
        </div>

        {history.length > 0 && (
          <div style={{ borderTop: '1px solid #2a2a3e' }}>
            <button
              className="btn-link"
              onClick={() => setExpandedHistory(v => !v)}
              style={{ fontSize: 12, color: 'var(--text-muted)', padding: '8px 12px', display: 'inline-flex', alignItems: 'center', gap: 6 }}
            >
              <span style={{
                transition: 'transform 0.2s',
                transform: expandedHistory ? 'rotate(0deg)' : 'rotate(-90deg)',
                display: 'inline-block',
              }}>
                ▼
              </span>
              {t('settings.logging.historyCount', { count: history.length })}
            </button>
            <div style={{
              overflow: 'hidden',
              maxHeight: expandedHistory ? '400px' : '0',
              transition: 'max-height 0.3s ease-in-out',
            }}>
              <div style={{ padding: '0 16px 12px', display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 380, overflowY: 'auto' }}>
                {history.slice(-10).map((h, i) => {
                  const pct = h.token_pct ?? 0;
                  const iter = h.iteration ?? '?';
                  const tools = h.recent_tools_summary ?? t('settings.logging.noTools');
                  const decision = h.decision_action && h.decision_action !== 'continue'
                    ? h.decision_action : '';
                  const err = h.last_error ?? '';
                  const stop = h.should_stop ? (h.stop_reason || '停止') : '';
                  const stuck = h.stuck_iterations > 0 ? h.stuck_iterations : 0;
                  const tokens = h.tokens_used ?? 0;

                  const badge = (text: string, color: string, bg: string) => (
                    <span style={{
                      display: 'inline-block',
                      padding: '1px 6px',
                      borderRadius: 4,
                      fontSize: 10,
                      fontWeight: 600,
                      color,
                      background: bg,
                      marginRight: 4,
                    }}>{text}</span>
                  );

                  return (
                    <div key={i} style={{
                      background: 'var(--bg-panel)',
                      border: '1px solid var(--border-panel)',
                      borderRadius: 8,
                      padding: '8px 12px',
                      opacity: expandedHistory ? 1 : 0,
                      transform: expandedHistory ? 'translateY(0)' : 'translateY(-8px)',
                      transition: 'opacity 0.25s ease, transform 0.25s ease',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                        <span style={{ color: 'var(--text-muted-light)', fontSize: 11, fontFamily: 'monospace', minWidth: 32 }}>
                          #{iter}
                        </span>
                        <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                          {tokens.toLocaleString()} Token
                        </span>
                        <span style={{ color: pct > 80 ? 'var(--status-danger-bright)' : pct > 50 ? 'var(--status-warning-bright)' : 'var(--status-success-bright)', fontSize: 11, fontWeight: 600 }}>
                          {pct}%
                        </span>
                        {stuck > 0 && badge(`卡住 ${stuck} 轮`, 'var(--status-warning-bright)', 'var(--bg-badge-warning)')}
                        {stop && badge(t('settings.logging.stopped'), 'var(--status-danger-bright)', 'var(--bg-badge-danger)')}
                        {decision && badge(decision, 'var(--status-info)', 'var(--bg-badge-info)')}
                      </div>
                      <div style={{ color: 'var(--text-muted-dark)', fontSize: 11, lineHeight: 1.6 }}>
                        {err && <span style={{ color: 'var(--status-danger-bright)' }}>Warning: {err} &nbsp;</span>}
                        {stop && <span style={{ color: 'var(--status-danger-bright)' }}>Stop: {stop} &nbsp;</span>}
                        <span style={{ color: 'var(--text-muted)' }}>Tools: {tools}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="form-actions">
        <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
          {saving ? t('common.saving') : saved ? `✓ ${t('common.saved')}` : t('settings.logging.save')}
        </button>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ 记忆系统 Tab
// ═════════════════════════════════════════════════════════════
const MemorySettings: React.FC = () => {
  const { t } = useTranslation();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const { saving, saved, doSave } = useSavingState();

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('memory')).then(data => {
      setConfig(data); setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const updateField = (path: string, value: any) => {
    setConfig(prev => {
      const keys = path.split('.');
      const next = { ...prev };
      let cur = next;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!cur[keys[i]]) cur[keys[i]] = {};
        cur[keys[i]] = { ...cur[keys[i]] };
        cur = cur[keys[i]];
      }
      cur[keys[keys.length - 1]] = value;
      return next;
    });
  };

  const handleSave = () => doSave(async () => {
    await putJSON(API.configUpdate('memory'), { value: config, persist: true });
  });

  const shortTerm = config.short_term || {};
  const sessionCompact = config.session_compact || {};
  const longTerm = config.long_term || {};

  return (
    <div className="settings-panel memory-settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <Icons.Brain size={16} /> {t('settings.memory.shortTermMemory')}

          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label={t('settings.memory.maxMessages')} desc={t('settings.memory.maxMessagesDesc')} />
            <input type="number" value={Number(shortTerm.max_history) || 50} min={10} max={200}
              onChange={e => updateField('short_term.max_history', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.autoCompactThreshold')} desc={t('settings.memory.autoCompactThresholdDesc')} />
            <input type="number" value={Number(shortTerm.auto_compact_threshold) || 10000} min={1000} max={100000}
              onChange={e => updateField('short_term.auto_compact_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.maxToolResults')} desc={t('settings.memory.maxToolResultsDesc')} />
            <input type="number" value={Number(shortTerm.max_tool_results) || 10} min={1} max={50}
              onChange={e => updateField('short_term.max_tool_results', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.resultTruncateLength')} desc={t('settings.memory.resultTruncateLengthDesc')} />
            <input type="number" value={Number(shortTerm.max_tool_result_length) || 500} min={100} max={5000}
              onChange={e => updateField('short_term.max_tool_result_length', parseInt(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">{t('settings.memory.sessionCompact')}</div>
          <label className="toggle-switch">
            <input type="checkbox" checked={!!sessionCompact.enabled} onChange={e => updateField('session_compact.enabled', e.target.checked)} />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{sessionCompact.enabled ? t('settings.memory.sessionCompactOn') : t('settings.memory.sessionCompactOff')}</span>
          </label>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label={t('settings.memory.tokenThreshold')} desc={t('settings.memory.tokenThresholdDesc')} />
            <input type="number" value={Number(sessionCompact.token_threshold) || 2000} min={500} max={10000}
              onChange={e => updateField('session_compact.token_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.toolCallThreshold')} desc={t('settings.memory.toolCallThresholdDesc')} />
            <input type="number" value={Number(sessionCompact.tool_call_threshold) || 3} min={1} max={20}
              onChange={e => updateField('session_compact.tool_call_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.keepRecentMessages')} desc={t('settings.memory.keepRecentMessagesDesc')} />
            <input type="number" value={Number(sessionCompact.keep_recent_messages) || 10} min={3} max={50}
              onChange={e => updateField('session_compact.keep_recent_messages', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.notesMaxLength')} desc={t('settings.memory.notesMaxLengthDesc')} />
            <input type="number" value={Number(sessionCompact.max_notes_length) || 2000} min={500} max={10000}
              onChange={e => updateField('session_compact.max_notes_length', parseInt(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">{t('settings.memory.longTermMemory')}</div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label={t('settings.memory.indexDbPath')} />
            <input type="text" value={longTerm.db_file || 'memory/memory_index.db'}
              onChange={e => updateField('long_term.db_file', e.target.value)} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.memoryStorageDir')} />
            <input type="text" value={config.memory_dir || 'memory'}
              onChange={e => updateField('memory_dir', e.target.value)} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.hybridRecall')} desc={t('settings.memory.hybridRecallDesc')} />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!longTerm.hybrid_enabled} onChange={e => updateField('long_term.hybrid_enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{longTerm.hybrid_enabled ? t('settings.memory.longTermHybridOn') : t('settings.memory.longTermHybridOff')}</span>
            </label>
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.embeddingDimensions')} />
            <input type="number" value={Number(longTerm.embedding_dimensions) || 96} min={16} max={1024}
              onChange={e => updateField('long_term.embedding_dimensions', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.defaultSchema')} />
            <CustomDropdown
              value={longTerm.default_schema || 'general'}
              items={[
                { value: 'general', label: 'general' },
                { value: 'profile', label: 'profile' },
                { value: 'project', label: 'project' },
                { value: 'issue', label: 'issue' },
              ]}
              onChange={val => updateField('long_term.default_schema', val)}
            />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.memory.allowSensitiveStore')} desc={t('settings.memory.allowSensitiveStoreDesc')} />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!longTerm.allow_sensitive_store} onChange={e => updateField('long_term.allow_sensitive_store', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{longTerm.allow_sensitive_store ? t('settings.memory.longTermAllowSensitiveOn') : t('settings.memory.longTermAllowSensitiveOff')}</span>
            </label>
          </div>
        </div>
      </div>

      <MemoryManagerPanel />

      <div className="form-actions">

        <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
          {saving ? t('common.saving') : saved ? `✓ ${t('common.saved')}` : t('settings.memory.save')}
        </button>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ 学习配置 Tab
// ═════════════════════════════════════════════════════════════
const LearningSettings: React.FC = () => {
  const { t } = useTranslation();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const { saving, saved, doSave } = useSavingState();

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('learning')).then(data => {
      setConfig(data); setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const updateField = (path: string, value: any) => {
    setConfig(prev => {
      const keys = path.split('.');
      const next = { ...prev };
      let cur = next;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!cur[keys[i]]) cur[keys[i]] = {};
        cur[keys[i]] = { ...cur[keys[i]] };
        cur = cur[keys[i]];
      }
      cur[keys[keys.length - 1]] = value;
      return next;
    });
  };

  const handleSave = () => doSave(async () => {
    await putJSON(API.configUpdate('learning'), { value: config, persist: true });
  });

  const decay = config.decay || {};
  const prior = config.prior || {};
  const policies = config.policies || {};
  const overridePolicy = config.override_policy || '';

  return (
    <div className="settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <Icons.BookOpen size={16} /> {t('settings.learning.title')}
          </div>
          <label className="toggle-switch">
            <input type="checkbox" checked={!!config.enabled} onChange={e => updateField('enabled', e.target.checked)} />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.enabled ? t('settings.learning.enabledOn') : t('settings.learning.enabledOff')}</span>
          </label>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label={t('settings.learning.policySelect')} desc={t('settings.learning.policySelectDesc')} />
            <CustomDropdown
              value={overridePolicy}
              items={[
                { value: '', label: t('settings.learning.autoLearning') },
                { value: 'default', label: t('settings.learning.defaultPolicy') },
                { value: 'efficient', label: t('settings.learning.efficientPolicy') },
                { value: 'aggressive', label: t('settings.learning.aggressivePolicy') },
              ]}
              onChange={val => updateField('override_policy', val || null)}
            />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.learning.statsPath')} desc={t('settings.learning.statsPathDesc')} />
            <input type="text" value={config.memory_path || 'data/learning/policy_bandit_stats.json'}
              onChange={e => updateField('memory_path', e.target.value)} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            {t('settings.learning.decayConfig')}
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label={t('settings.learning.decayInterval')} desc={t('settings.learning.decayIntervalDesc')} />
            <input type="number" value={Number(decay.interval) || 50} min={10} max={500}
              onChange={e => updateField('decay.interval', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.learning.decayFactor')} desc={t('settings.learning.decayFactorDesc')} />
            <input type="number" value={Number(decay.factor) || 0.99} min={0.9} max={1} step={0.01}
              onChange={e => updateField('decay.factor', parseFloat(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            {t('settings.learning.coldStartParams')}
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label={t('settings.learning.alpha')} />
            <input type="number" value={Number(prior.alpha) || 2.0} min={0.1} max={10} step={0.1}
              onChange={e => updateField('prior.alpha', parseFloat(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label={t('settings.learning.beta')} />
            <input type="number" value={Number(prior.beta) || 2.0} min={0.1} max={10} step={0.1}
              onChange={e => updateField('prior.beta', parseFloat(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            {t('settings.learning.policyTemplate')}
          </div>
        </div>
        <div className="settings-card-grid">
          {Object.entries(policies).map(([name, params]: [string, any]) => (
            <div key={name} className="policy-group">
              <div className="policy-header">{t(`settings.learning.policy${name.charAt(0).toUpperCase() + name.slice(1)}`)}</div>
              <div className="policy-params">
                <div className="form-group">
                  <FieldLabel label={t('settings.learning.stuckIterations')} />
                  <input type="number" value={Number(params.stuck_iterations) || 3} min={1} max={20}
                    onChange={e => updateField(`policies.${name}.stuck_iterations`, parseInt(e.target.value))} />
                </div>
                <div className="form-group">
                  <FieldLabel label={t('settings.learning.repetitionThreshold')} />
                  <input type="number" value={Number(params.repetition_threshold) || 3} min={1} max={20}
                    onChange={e => updateField(`policies.${name}.repetition_threshold`, parseInt(e.target.value))} />
                </div>
                <div className="form-group">
                  <FieldLabel label={t('settings.learning.trendWorseningThreshold')} />
                  <input type="number" value={Number(params.progress_trend_threshold) || -0.3} min={-1} max={0} step={0.1}
                    onChange={e => updateField(`policies.${name}.progress_trend_threshold`, parseFloat(e.target.value))} />
                </div>
                <div className="form-group">
                  <FieldLabel label={t('settings.learning.stopConfirmConfidence')} />
                  <input type="number" value={Number(params.confirm_stop_threshold) || 0.9} min={0.5} max={1} step={0.05}
                    onChange={e => updateField(`policies.${name}.confirm_stop_threshold`, parseFloat(e.target.value))} />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="form-actions">
        <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
          {saving ? t('common.saving') : saved ? `✓ ${t('common.saved')}` : t('settings.learning.save')}
        </button>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ 启发式引擎预设配置
const HEURISTICS_PRESETS = {
  'minimal': {
    label: 'Minimal',
    descriptionKey: 'settings.heuristics.presets.minimalDesc',
    config: {
      enabled: true,
      log_level: 'warning',
      trace_enabled: false,
      thresholds: {
        max_iterations_ratio: 0.95,
        token_budget_ratio: 0.98,
        stuck_iterations: 10,
        repetition_threshold: 8,
        ema_alpha: 0.2,
        plateau_stuck_limit: 15,
      },
      rules: {
        'term-001': { enabled: false },
        'term-002': { enabled: true, threshold: 0.95 },
        'term-003': { enabled: true, params: { threshold: 8 } },
        'term-004': { enabled: true, params: { stuck_threshold: 10, trend_threshold: -0.5 } },
        'loop-001': { enabled: true, params: { threshold: 8 } },
        'loop-002': { enabled: true },
        'loop-003': { enabled: false },
      },
    },
  },
  'balanced': {
    label: 'Balanced',
    descriptionKey: 'settings.heuristics.presets.balancedDesc',
    config: {
      enabled: true,
      log_level: 'info',
      trace_enabled: false,
      thresholds: {
        max_iterations_ratio: 0.85,
        token_budget_ratio: 0.9,
        stuck_iterations: 5,
        repetition_threshold: 4,
        ema_alpha: 0.3,
        plateau_stuck_limit: 8,
      },
      rules: {
        'term-001': { enabled: false },
        'term-002': { enabled: true, threshold: 0.9 },
        'term-003': { enabled: true, params: { threshold: 4 } },
        'term-004': { enabled: true, params: { stuck_threshold: 5, trend_threshold: -0.3 } },
        'loop-001': { enabled: true, params: { threshold: 4 } },
        'loop-002': { enabled: true },
        'loop-003': { enabled: false },
      },
    },
  },
  'efficient': {
    label: 'Efficient',
    descriptionKey: 'settings.heuristics.presets.efficientDesc',
    config: {
      enabled: true,
      log_level: 'info',
      trace_enabled: false,
      thresholds: {
        max_iterations_ratio: 0.8,
        token_budget_ratio: 0.85,
        stuck_iterations: 3,
        repetition_threshold: 2,
        ema_alpha: 0.4,
        plateau_stuck_limit: 5,
      },
      rules: {
        'term-001': { enabled: false },
        'term-002': { enabled: true, threshold: 0.85 },
        'term-003': { enabled: true, params: { threshold: 3 } },
        'term-004': { enabled: true, params: { stuck_threshold: 3, trend_threshold: -0.2 } },
        'loop-001': { enabled: true, params: { threshold: 3 } },
        'loop-002': { enabled: true },
        'loop-003': { enabled: false },
      },
    },
  },
  'cautious': {
    label: 'Cautious',
    descriptionKey: 'settings.heuristics.presets.cautiousDesc',
    config: {
      enabled: true,
      log_level: 'info',
      trace_enabled: true,
      thresholds: {
        max_iterations_ratio: 0.9,
        token_budget_ratio: 0.92,
        stuck_iterations: 4,
        repetition_threshold: 3,
        ema_alpha: 0.25,
        plateau_stuck_limit: 6,
      },
      rules: {
        'term-001': { enabled: false },
        'term-002': { enabled: true, threshold: 0.92 },
        'term-003': { enabled: true, params: { threshold: 3 } },
        'term-004': { enabled: true, params: { stuck_threshold: 4, trend_threshold: -0.25 } },
        'loop-001': { enabled: true, params: { threshold: 3 } },
        'loop-002': { enabled: true },
        'loop-003': { enabled: false },
      },
    },
  },
  'disabled': {
    label: 'Disabled',
    descriptionKey: 'settings.heuristics.presets.disabledDesc',
    config: {
      enabled: false,
      log_level: 'info',
      trace_enabled: false,
      thresholds: {
        max_iterations_ratio: 0.8,
        token_budget_ratio: 0.9,
        stuck_iterations: 3,
        repetition_threshold: 3,
        ema_alpha: 0.3,
        plateau_stuck_limit: 5,
      },
      rules: {
        'term-001': { enabled: false },
        'term-002': { enabled: false },
        'term-003': { enabled: false },
        'term-004': { enabled: false },
        'loop-001': { enabled: false },
        'loop-002': { enabled: false },
        'loop-003': { enabled: false },
      },
    },
  },
};

type PresetKey = Exclude<keyof typeof HEURISTICS_PRESETS, number>;

// ★ 启发式引擎 Tab
// ═════════════════════════════════════════════════════════════
const HeuristicsSettings: React.FC = () => {
  const { t } = useTranslation();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [selectedPreset, setSelectedPreset] = useState<PresetKey | 'custom'>('balanced');
  const { saving, saved, doSave } = useSavingState();

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('heuristics')).then(data => {
      setConfig(data);
      setLoading(false);
      const matched = findMatchingPreset(data);
      setSelectedPreset(matched);
    }).catch(() => setLoading(false));
  }, []);

  const findMatchingPreset = (cfg: Record<string, any>): PresetKey | 'custom' => {
    const cfgPreset = extractPresetFields(cfg);
    for (const [key, preset] of Object.entries(HEURISTICS_PRESETS) as [PresetKey, typeof HEURISTICS_PRESETS[PresetKey]][]) {
      const presetFields = extractPresetFields(preset.config);
      if (deepEqual(cfgPreset, presetFields)) {
        return key;
      }
    }
    return 'custom';
  };

  const extractPresetFields = (cfg: Record<string, any>) => ({
    enabled: cfg.enabled,
    log_level: cfg.log_level,
    trace_enabled: cfg.trace_enabled,
    thresholds: cfg.thresholds ? {
      max_iterations_ratio: cfg.thresholds.max_iterations_ratio,
      token_budget_ratio: cfg.thresholds.token_budget_ratio,
      stuck_iterations: cfg.thresholds.stuck_iterations,
      repetition_threshold: cfg.thresholds.repetition_threshold,
      ema_alpha: cfg.thresholds.ema_alpha,
      plateau_stuck_limit: cfg.thresholds.plateau_stuck_limit,
    } : undefined,
    rules: cfg.rules,
  });

  const deepEqual = (a: any, b: any): boolean => {
    if (a === b) return true;
    if (typeof a !== typeof b) return false;
    if (typeof a !== 'object' || a === null || b === null) return false;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    if (Array.isArray(a)) {
      if (a.length !== b.length) return false;
      return a.every((item, i) => deepEqual(item, b[i]));
    }
    const keysA = Object.keys(a);
    const keysB = Object.keys(b);
    if (keysA.length !== keysB.length) return false;
    return keysA.every(key => deepEqual(a[key], b[key]));
  };

  const updateField = (path: string, value: any) => {
    setSelectedPreset('custom');
    setConfig(prev => {
      const keys = path.split('.');
      const next = { ...prev };
      let cur = next;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!cur[keys[i]]) cur[keys[i]] = {};
        cur[keys[i]] = { ...cur[keys[i]] };
        cur = cur[keys[i]];
      }
      cur[keys[keys.length - 1]] = value;
      return next;
    });
  };

  const applyPreset = (presetKey: PresetKey) => {
    const preset = HEURISTICS_PRESETS[presetKey];
    setConfig(preset.config);
    setSelectedPreset(presetKey);
  };

  const handleSave = () => doSave(async () => {
    await putJSON(API.configUpdate('heuristics'), { value: config, persist: true });
  });

  const thresholds = config.thresholds || {};

  if (loading) {
    return <div className="settings-panel"><div className="settings-loading"><span className="loading-dots"><span></span><span></span><span></span></span> {t('common.loading')}</div></div>;
  }

  return (
    <div className="settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            {t('settings.heuristics.engineDecision')}
          </div>
        </div>
        <div className="preset-grid">
          {(Object.entries(HEURISTICS_PRESETS) as [PresetKey, typeof HEURISTICS_PRESETS[PresetKey]][]).map(([key, preset]) => (
            <div
              key={key}
              className={`preset-card ${selectedPreset === key ? 'selected' : ''}`}
              onClick={() => applyPreset(key)}
            >
              <div className="preset-label">{preset.label}</div>
              <div className="preset-desc">{t(preset.descriptionKey)}</div>
            </div>
          ))}
        </div>
      </div>

      <Collapsible summary={t('settings.heuristics.advancedConfig')} className="settings-collapsible">
        <div className="settings-card">
          <div className="settings-card-header">
            <div className="settings-card-title">{t('settings.heuristics.logAndTrace')}</div>
          </div>
          <div className="settings-card-grid">
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.logLevel')} />
              <CustomDropdown
                value={(config.log_level || 'info').toUpperCase()}
                items={[
                  { value: 'DEBUG', label: 'DEBUG' },
                  { value: 'INFO', label: 'INFO' },
                  { value: 'WARNING', label: 'WARNING' },
                  { value: 'ERROR', label: 'ERROR' },
                ]}
                onChange={val => updateField('log_level', val.toLowerCase())}
              />
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.decisionTrace')} />
              <label className="toggle-switch">
                <input type="checkbox" checked={!!config.trace_enabled} onChange={e => updateField('trace_enabled', e.target.checked)} />
                <span className="toggle-slider"></span>
              </label>
            </div>
          </div>
        </div>

        <div className="settings-card">
          <div className="settings-card-header">
            <div className="settings-card-title">{t('settings.heuristics.globalThresholds')}</div>
          </div>
          <div className="settings-card-grid">
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.iterationWarningRatio')} desc={t('settings.heuristics.iterationWarningRatioDesc')} />
              <input type="number" value={Number(thresholds.max_iterations_ratio) || 0.8} min={0.5} max={1} step={0.05}
                onChange={e => updateField('thresholds.max_iterations_ratio', parseFloat(e.target.value))} />
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.tokenBudgetWarningRatio')} desc={t('settings.heuristics.tokenBudgetWarningRatioDesc')} />
              <input type="number" value={Number(thresholds.token_budget_ratio) || 0.9} min={0.5} max={1} step={0.05}
                onChange={e => updateField('thresholds.token_budget_ratio', parseFloat(e.target.value))} />
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.noProgressIterations')} desc={t('settings.heuristics.noProgressIterationsDesc')} />
              <input type="number" value={Number(thresholds.stuck_iterations) || 3} min={1} max={20}
                onChange={e => updateField('thresholds.stuck_iterations', parseInt(e.target.value))} />
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.repetitionThreshold')} desc={t('settings.heuristics.repetitionThresholdDesc')} />
              <input type="number" value={Number(thresholds.repetition_threshold) || 3} min={1} max={20}
                onChange={e => updateField('thresholds.repetition_threshold', parseInt(e.target.value))} />
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.emaSmoothingFactor')} desc={t('settings.heuristics.emaSmoothingFactorDesc')} />
              <input type="number" value={Number(thresholds.ema_alpha) || 0.3} min={0.1} max={1} step={0.05}
                onChange={e => updateField('thresholds.ema_alpha', parseFloat(e.target.value))} />
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.plateauStuckLimit')} desc={t('settings.heuristics.plateauStuckLimitDesc')} />
              <input type="number" value={Number(thresholds.plateau_stuck_limit) || 5} min={1} max={30}
                onChange={e => updateField('thresholds.plateau_stuck_limit', parseInt(e.target.value))} />
            </div>
          </div>
        </div>

        <div className="settings-card">
          <div className="settings-card-header">
            <div className="settings-card-title">{t('settings.heuristics.ruleSwitches')}</div>
          </div>
          <div className="settings-card-grid">
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.tokenBudgetProtection')} />
              <label className="toggle-switch">
                <input type="checkbox" checked={!!(config.rules?.['term-002']?.enabled)} onChange={e => updateField('rules.term-002.enabled', e.target.checked)} />
                <span className="toggle-slider"></span>
              </label>
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.emptyResultChainDetection')} />
              <label className="toggle-switch">
                <input type="checkbox" checked={!!(config.rules?.['term-003']?.enabled)} onChange={e => updateField('rules.term-003.enabled', e.target.checked)} />
                <span className="toggle-slider"></span>
              </label>
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.noProgressDetection')} />
              <label className="toggle-switch">
                <input type="checkbox" checked={!!(config.rules?.['term-004']?.enabled)} onChange={e => updateField('rules.term-004.enabled', e.target.checked)} />
                <span className="toggle-slider"></span>
              </label>
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.repetitiveToolCalls')} />
              <label className="toggle-switch">
                <input type="checkbox" checked={!!(config.rules?.['loop-001']?.enabled)} onChange={e => updateField('rules.loop-001.enabled', e.target.checked)} />
                <span className="toggle-slider"></span>
              </label>
            </div>
            <div className="form-group">
              <FieldLabel label={t('settings.heuristics.patternLoopDetection')} />
              <label className="toggle-switch">
                <input type="checkbox" checked={!!(config.rules?.['loop-002']?.enabled)} onChange={e => updateField('rules.loop-002.enabled', e.target.checked)} />
                <span className="toggle-slider"></span>
              </label>
            </div>
          </div>
        </div>
      </Collapsible>

      <div className="form-actions">
        <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
          {saving ? t('common.saving') : saved ? `✓ ${t('common.saved')}` : t('settings.heuristics.save')}
        </button>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// 外观设置 Tab
// ═════════════════════════════════════════════════════════════
const AppearanceSettings: React.FC = () => {
  const { t } = useTranslation();
  const { theme, setTheme, language, setLanguage } = useAppStore();

  const themes: { value: Theme; label: string }[] = [
    { value: 'auto', label: t('settings.theme.auto') },
    { value: 'light', label: t('settings.theme.light') },
    { value: 'dark', label: t('settings.theme.dark') },
  ];

  const languages: { value: Language; label: string }[] = [
    { value: 'zh-CN', label: t('settings.language.zhCN') },
    { value: 'zh-TW', label: t('settings.language.zhTW') },
    { value: 'en', label: t('settings.language.en') },
  ];

  return (
    <div className="settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <span className="settings-card-title">
            <Icons.Globe size={18} />
            {t('settings.language.title')}
          </span>
        </div>
        <div className="form-group">
          <FieldLabel label={t('settings.language.title')} />
          <div className="settings-options-grid">
            {languages.map((lang) => (
              <label
                key={lang.value}
                className={`settings-option-card ${language === lang.value ? 'selected' : ''}`}
              >
                <input
                  type="radio"
                  name="language"
                  value={lang.value}
                  checked={language === lang.value}
                  onChange={() => setLanguage(lang.value)}
                  style={{ display: 'none' }}
                />
                <span className="option-label">{lang.label}</span>
              </label>
            ))}
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <span className="settings-card-title">
            <Icons.Theme size={18} />
            {t('settings.theme.title')}
          </span>
        </div>
        <div className="form-group">
          <FieldLabel label={t('settings.theme.title')} />
          <div className="theme-options-grid">
            {themes.map((t) => (
              <label
                key={t.value}
                className={`theme-option-card ${theme === t.value ? 'selected' : ''}`}
              >
                <input
                  type="radio"
                  name="theme"
                  value={t.value}
                  checked={theme === t.value}
                  onChange={() => setTheme(t.value)}
                  style={{ display: 'none' }}
                />
                <div className={`theme-preview ${t.value === 'auto' ? 'auto-theme' : t.value}`}>
                  {t.value === 'auto' ? (
                    <>
                      <div className="theme-half light"></div>
                      <div className="theme-half dark"></div>
                    </>
                  ) : null}
                </div>
                <span className="theme-label">{t.label}</span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ SettingsPage 主组件
// ═════════════════════════════════════════════════════════════
export const SettingsPage: React.FC = () => {
  const { settingsTab, setSettingsTab, setShowSettingsPage } = useAppStore();

  const renderContent = () => {
    switch (settingsTab) {
      case 'model': return <ModelSettings />;
      case 'agent': return <AgentSettings />;
      case 'memory': return <MemorySettings />;
      case 'learning': return <LearningSettings />;
      case 'heuristics': return <HeuristicsSettings />;
      case 'security': return <SecuritySettings />;
      case 'channel': return <ChannelSettings />;
      case 'logging': return <LoggingSettings />;
      case 'appearance': return <AppearanceSettings />;
      default: return null;
    }
  };

  const SETTINGS_TABS = useSettingsTabs();
  const { t } = useTranslation();

  return (
    <div className="settings-page">
      {/* 头部 */}
      <div className="settings-header">
        <h2><Icons.Settings size={20} /> {t('settings.title')}</h2>
        <button
          className="btn-back-chat"
          onClick={() => setShowSettingsPage(false)}
          title={t('chat.backToChat')}
        >
          <Icons.Chat size={18} />
          <span>{t('chat.backToChat')}</span>
        </button>
      </div>

      <div className="settings-body">
        {/* 左侧导航 */}
        <nav className="settings-nav">
          {SETTINGS_TABS.map(tab => (
            <button
              key={tab.id}
              className={`settings-nav-item ${settingsTab === tab.id ? 'active' : ''}`}
              onClick={() => setSettingsTab(tab.id)}
            >
              <tab.Icon size={18} />
              <span>{tab.label}</span>
            </button>
          ))}
        </nav>

        {/* 右侧内容区 */}
        <main className="settings-content">
          {renderContent()}
        </main>
      </div>
    </div>
  );
};
