import React, { useState, useEffect, useCallback } from 'react';
import { useAppStore } from '../stores/appStore';
import { API, fetchJSON, postJSON, putJSON } from '../utils/api';
import { Icons } from './Icons';
import { CustomDropdown } from './CustomDropdown';
import { MemoryManagerPanel } from './MemoryManagerPanel';


// ── 设置 Tab 定义 ───────────────────────────────────────────
const SETTINGS_TABS = [
  { id: 'model', label: '模型配置', Icon: Icons.Database },
  { id: 'agent', label: 'Agent 行为', Icon: Icons.Bot },
  { id: 'memory', label: '记忆系统', Icon: Icons.Brain },
  { id: 'learning', label: '学习配置', Icon: Icons.BookOpen },
  { id: 'heuristics', label: '启发式引擎', Icon: Icons.Lightbulb },
  { id: 'security', label: '安全策略', Icon: Icons.Shield },
  { id: 'channel', label: '通道配置', Icon: Icons.Globe },
  { id: 'logging', label: '日志设置', Icon: Icons.FileText },
  { id: 'server', label: '服务端 & 网络', Icon: Icons.Globe },
] as const;

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
        <span><Icons.Database size={18} /> LLM 模型配置</span>
      </h3>
      <p className="settings-desc">添加多个模型配置，保存后选择当前使用的模型。</p>

      {loading ? (
        <div className="settings-loading"><span className="loading-dots"><span></span><span></span><span></span></span> 加载中...</div>
      ) : (
        <>
          <div className="settings-grid">
            <div className="form-group">
              <FieldLabel label="当前使用模型" />
              <CustomDropdown
                value={displayModelName}
                items={models.map((m: any) => ({ value: m.name, label: `${m.name} (${m.model || '未设置'})` }))}
                onChange={val => updateField('current_model', val)}
              />
            </div>

            <div className="form-group">
              <FieldLabel label="流式输出" />
              <label className="toggle-switch">
                <input type="checkbox" checked={!!streaming.enabled} onChange={e => updateField('streaming.enabled', e.target.checked)} />
                <span className="toggle-slider"></span>
                <span className="toggle-label">{streaming.enabled ? '已开启' : '已关闭'}</span>
              </label>
            </div>
          </div>

          <div className="models-list">
            <div className="models-list-header">
              <h4>模型配置列表</h4>
              <button className="btn-small" onClick={addModel}>+ 添加模型</button>
            </div>

            {models.length === 0 && (
              <p className="settings-desc">暂无模型配置，点击上方"添加模型"按钮添加。</p>
            )}

            {models.map((model: any, index: number) => (
              <div key={index} className="model-card">
                <div className="model-card-header">
                  <input
                    type="text"
                    value={model.name}
                    onChange={e => updateModelField(index, 'name', e.target.value)}
                    placeholder="模型名称"
                    className="model-name-input"
                  />
                  <button className="btn-icon" onClick={() => removeModel(index)} title="删除">
                    <Icons.X size={16} />
                  </button>
                </div>
                <div className="model-card-body">
                  <div className="form-group">
                    <FieldLabel label="API 地址" />
                    <input type="text" value={model.base_url || ''} onChange={e => updateModelField(index, 'base_url', e.target.value)} placeholder="https://api.openai.com/v1" />
                  </div>
                  <div className="form-group">
                    <FieldLabel label="API Key" />
                    <input type="password" value={model.api_key || ''} onChange={e => updateModelField(index, 'api_key', e.target.value)} placeholder="sk-..." />
                  </div>
                  <div className="form-group">
                    <FieldLabel label="模型 ID" />
                    <input type="text" value={model.model || ''} onChange={e => updateModelField(index, 'model', e.target.value)} placeholder="gpt-4o" />
                  </div>
                  <div className="form-row">
                    <div className="form-group">
                      <FieldLabel label="Temperature" />
                      <input type="number" value={Number(model.temperature) || 0.7} min={0} max={2} step={0.1}
                        onChange={e => updateModelField(index, 'temperature', parseFloat(e.target.value))} />
                    </div>
                    <div className="form-group">
                      <FieldLabel label="超时 (秒)" />
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
              {saving ? '保存中...' : saved ? '✓ 已保存并重载' : '保存配置 & 重载引擎'}
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
            <Icons.Bot size={16} /> Agent 行为配置
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="限制迭代次数" desc="开启后将强制限制 Agent 的最大迭代次数" />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!config.enforce_iteration_limit} onChange={e => updateField('enforce_iteration_limit', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{config.enforce_iteration_limit ? '已启用限制' : '无限制'}</span>
            </label>
          </div>
          {config.enforce_iteration_limit && (
            <div className="form-group">
              <FieldLabel label="最大迭代次数" desc="Agent 执行的最大迭代次数上限" />
              <input type="number" value={Number(config.max_iterations) || 10} min={1} max={100}
                onChange={e => updateField('max_iterations', parseInt(e.target.value))} />
            </div>
          )}
          <div className="form-group">
            <FieldLabel label="请求超时 (秒)" />
            <input type="number" value={Number(config.request_timeout) || 300} min={30} max={3600}
              onChange={e => updateField('request_timeout', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="Flash 模式" desc="开启后跳过窗口上下文注入，减少 token 消耗" />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!config.flash_mode} onChange={e => updateField('flash_mode', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{config.flash_mode ? '已开启' : '已关闭'}</span>
            </label>
          </div>
          <div className="form-group">
            <FieldLabel label="Shell 工作目录" desc="Agent 执行命令的默认目录，留空则使用进程当前目录" />
            <input type="text" value={config.shell_cwd || ''} placeholder="如: D:\\projects 或 /home/user/projects"
              onChange={e => updateField('shell_cwd', e.target.value)} />
          </div>
        </div>
        <div className="form-actions">
          <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : saved ? '✓ 已保存' : '保存 Agent 配置'}
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
            <Icons.Shield size={16} /> 安全策略
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="权限等级" />
            <CustomDropdown
              value={config.permission_level || 'standard'}
              items={[
                { value: 'read_only', label: '只读模式' },
                { value: 'standard', label: '标准模式' },
                { value: 'admin', label: '管理员' },
                { value: 'unrestricted', label: '无限制' },
              ]}
              onChange={val => updateField('permission_level', val)}
            />
          </div>
          <div className="form-group">
            <FieldLabel label="命令超时 (秒)" />
            <input type="number" value={Number(config.command_timeout) || 30} min={5} max={300}
              onChange={e => updateField('command_timeout', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="Shell 硬超时 (秒)" />
            <input type="number" value={Number(config.shell_hard_timeout) || 60} min={10} max={600}
              onChange={e => updateField('shell_hard_timeout', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="输出截断 (字节)" />
            <input type="number" value={Number(config.max_output_bytes) || 8192} min={1024} max={1048576}
              onChange={e => updateField('max_output_bytes', parseInt(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            命令黑名单
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
              placeholder="输入危险命令后回车添加..." />
            <button className="btn-secondary btn-sm" onClick={addBlacklistItem}>添加</button>
          </div>
        </div>
      </div>

      <div className="form-actions">
        <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
          {saving ? '保存中...' : saved ? '✓ 已保存' : '保存安全配置'}
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
  const updateField = (field: string, value: any) => {
    onChange({ ...config, [field]: value });
  };

  return (
    <div className="settings-card" style={{ marginBottom: 24 }}>
      <div className="settings-card-header">
        <div className="settings-card-title">
          <Icons.Globe size={16} /> QQ 机器人通道
        </div>
        <button className="btn-primary btn-sm" onClick={onSave} disabled={saving}>
          {saving ? '保存中...' : saved ? '已保存!' : '保存配置'}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="settings-card-grid">
        <div className="form-group">
          <FieldLabel label="启用通道" />
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={config.enabled !== false}
              onChange={e => updateField('enabled', e.target.checked)}
            />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.enabled !== false ? '已启用' : '已禁用'}</span>
          </label>
        </div>

        <div className="form-group">
          <FieldLabel label="自动启动" />
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={config.auto_start !== false}
              onChange={e => updateField('auto_start', e.target.checked)}
            />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.auto_start !== false ? '已开启' : '已关闭'}</span>
          </label>
        </div>

        <div className="form-group">
          <FieldLabel label="App ID" />
          <input
            type="text"
            value={config.app_id || ''}
            onChange={e => updateField('app_id', e.target.value)}
            placeholder="从环境变量 QQ_BOT_APP_ID 读取"
          />
        </div>

        <div className="form-group">
          <FieldLabel label="App Secret" />
          <input
            type="password"
            value={config.app_secret || ''}
            onChange={e => updateField('app_secret', e.target.value)}
            placeholder="从环境变量 QQ_BOT_APP_SECRET 读取"
          />
        </div>

        <div className="form-group">
          <FieldLabel label="Intents 值" />
          <input
            type="number"
            value={config.intents || 1107296256}
            onChange={e => updateField('intents', parseInt(e.target.value))}
          />
        </div>

        <div className="form-group">
          <FieldLabel label="凭证状态" />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: config.app_id && config.app_secret ? '#22c55e' : '#ef4444',
            }} />
            <span style={{ color: '#a8b1c2', fontSize: 13 }}>
              {config.app_id && config.app_secret ? '已配置凭证' : '缺少凭证（请配置或设置环境变量）'}
            </span>
          </div>
        </div>
      </div>

      <div className="settings-card-footer">
        <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>
          提示：保存配置后将自动热重载通道连接。凭证也可以通过环境变量 QQ_BOT_APP_ID 和 QQ_BOT_APP_SECRET 设置。
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
          <Icons.Globe size={16} /> Telegram Bot 通道
        </div>
        <button className="btn-primary btn-sm" onClick={onSave} disabled={saving}>
          {saving ? '保存中...' : saved ? '已保存!' : '保存配置'}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="settings-card-grid">
        <div className="form-group">
          <FieldLabel label="启用通道" />
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={config.enabled === true}
              onChange={e => updateField('enabled', e.target.checked)}
            />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.enabled === true ? '已启用' : '已禁用'}</span>
          </label>
        </div>

        <div className="form-group">
          <FieldLabel label="自动启动" />
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={config.auto_start !== false}
              onChange={e => updateField('auto_start', e.target.checked)}
            />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.auto_start !== false ? '已开启' : '已关闭'}</span>
          </label>
        </div>

        <div className="form-group" style={{ gridColumn: 'span 2' }}>
          <FieldLabel label="Bot Token" desc="从 @BotFather 获取" />
          <input
            type="password"
            value={config.bot_token || ''}
            onChange={e => updateField('bot_token', e.target.value)}
            placeholder="从环境变量 TELEGRAM_BOT_TOKEN 读取"
          />
        </div>

        <div className="form-group">
          <FieldLabel label="白名单用户 ID" desc="可选，逗号分隔" />
          <input
            type="text"
            value={(config.whitelist_user_ids || []).join(', ')}
            onChange={e => updateListField('whitelist_user_ids', e.target.value)}
            placeholder="例如: 123456789, 987654321"
          />
        </div>

        <div className="form-group">
          <FieldLabel label="白名单用户名" desc="可选，逗号分隔，不含@" />
          <input
            type="text"
            value={(config.whitelist_usernames || []).join(', ')}
            onChange={e => updateListField('whitelist_usernames', e.target.value)}
            placeholder="例如: username1, username2"
          />
        </div>

        <div className="form-group">
          <FieldLabel label="凭证状态" />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: config.bot_token ? '#22c55e' : '#ef4444',
            }} />
            <span style={{ color: '#a8b1c2', fontSize: 13 }}>
              {config.bot_token ? '已配置凭证' : '缺少凭证（请配置或设置环境变量）'}
            </span>
          </div>
        </div>
      </div>

      <div className="settings-card-footer">
        <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>
          提示：保存配置后将自动热重载通道连接。Token 也可以通过环境变量 TELEGRAM_BOT_TOKEN 设置。
        </p>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// 通道配置 Tab (多平台消息入口)
// ═════════════════════════════════════════════════════════════
const ChannelSettings: React.FC = () => {
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
      setErrors(prev => ({ ...prev, [platform]: e.message || '保存失败' }));
    } finally {
      setSaving(prev => ({ ...prev, [platform]: false }));
    }
  };

  const updateConfig = (platform: string, config: Record<string, any>) => {
    setConfigs(prev => ({ ...prev, [platform]: config }));
  };

  if (loading) {
    return <div className="settings-card"><div className="settings-loading"><span className="loading-dots"><span></span><span></span><span></span></span> 加载中...</div></div>;
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
            <Icons.FileText size={16} /> 日志设置
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="日志级别" />
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
            <FieldLabel label="控制台输出" />
            <label className="toggle-switch">
              <input type="checkbox" checked={config.console !== false} onChange={e => updateField('console', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{config.console !== false ? '已开启' : '已关闭'}</span>
            </label>
          </div>
          <div className="form-group">
            <FieldLabel label="缓冲区大小 (条目)" />
            <input type="number" value={Number(config.max_size) || 5000} min={100} max={100000}
              onChange={e => updateField('max_size', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="日志文件路径" />
            <input type="text" value={config.file || ''} onChange={e => updateField('file', e.target.value)} placeholder="留空则不写入文件" />
          </div>
          <div className="form-group">
            <FieldLabel label="备份文件数量" />
            <input type="number" value={Number(config.backup_count) || 5} min={0} max={20}
              onChange={e => updateField('backup_count', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="日志格式" />
            <input type="text" value={config.format || ''} onChange={e => updateField('format', e.target.value)} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <Icons.Zap size={16} /> Agent 运行状态
          </div>
          <button className="btn-secondary btn-sm" onClick={() => { loadStatus(); loadHistory(); }} disabled={statusLoading}>
            {statusLoading ? '刷新中...' : '刷新'}
          </button>
        </div>
        <div style={{ padding: '12px 16px' }}>
          {!status || !status.available ? (
            <div style={{ color: '#888', fontSize: 13 }}>Agent 未在运行</div>
          ) : (
            <pre style={{
              margin: 0,
              color: '#a8b1c2',
              fontSize: 12,
              fontFamily: 'monospace',
              background: '#0f1117',
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
              style={{ fontSize: 12, color: '#6b7280', padding: '8px 12px', display: 'inline-flex', alignItems: 'center', gap: 6 }}
            >
              <span style={{
                transition: 'transform 0.2s',
                transform: expandedHistory ? 'rotate(0deg)' : 'rotate(-90deg)',
                display: 'inline-block',
              }}>
                ▼
              </span>
              历史记录 ({history.length})
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
                  const tools = h.recent_tools_summary ?? '无工具';
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
                      background: '#1a1a28',
                      border: '1px solid #2a2a3e',
                      borderRadius: 8,
                      padding: '8px 12px',
                      opacity: expandedHistory ? 1 : 0,
                      transform: expandedHistory ? 'translateY(0)' : 'translateY(-8px)',
                      transition: 'opacity 0.25s ease, transform 0.25s ease',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                        <span style={{ color: '#9ca3af', fontSize: 11, fontFamily: 'monospace', minWidth: 32 }}>
                          #{iter}
                        </span>
                        <span style={{ color: '#6b7280', fontSize: 11 }}>
                          {tokens.toLocaleString()} Token
                        </span>
                        <span style={{ color: pct > 80 ? '#ef4444' : pct > 50 ? '#f59e0b' : '#10b981', fontSize: 11, fontWeight: 600 }}>
                          {pct}%
                        </span>
                        {stuck > 0 && badge(`卡住 ${stuck} 轮`, '#f59e0b', 'rgba(245,158,11,0.15)')}
                        {stop && badge('已停止', '#ef4444', 'rgba(239,68,68,0.15)')}
                        {decision && badge(decision, '#8b5cf6', 'rgba(139,92,246,0.15)')}
                      </div>
                      <div style={{ color: '#4b5563', fontSize: 11, lineHeight: 1.6 }}>
                        {err && <span style={{ color: '#ef4444' }}>Warning: {err} &nbsp;</span>}
                        {stop && <span style={{ color: '#ef4444' }}>Stop: {stop} &nbsp;</span>}
                        <span style={{ color: '#6b7280' }}>Tools: {tools}</span>
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
          {saving ? '保存中...' : saved ? '✓ 已保存' : '保存日志配置'}
        </button>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ 服务端 & 网络 Tab
// ═════════════════════════════════════════════════════════════
const ServerSettings: React.FC = () => {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('server')).then(data => {
      setConfig(data); setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const corsOrigins = Array.isArray(config.cors_origins) ? config.cors_origins.join(', ') : (config.cors_origins || '*');
  const corsMethods = Array.isArray(config.cors_methods) ? config.cors_methods.join(', ') : (config.cors_methods || '*');
  const corsHeaders = Array.isArray(config.cors_headers) ? config.cors_headers.join(', ') : (config.cors_headers || '*');

  return (
    <div className="settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <Icons.Globe size={16} /> 服务端 & 网络配置
          </div>
          <span className="badge-restart">需重启生效</span>
        </div>
        <div className="settings-card-grid">
          <div className="form-group form-readonly">
            <FieldLabel label="监听地址 (Host)" />
            <input type="text" value={config.host || 'N/A'} readOnly />
            <small className="field-hint">需改 server.yaml 后重启</small>
          </div>
          <div className="form-group form-readonly">
            <FieldLabel label="监听端口 (Port)" />
            <input type="text" value={String(config.port || 'N/A')} readOnly />
            <small className="field-hint">需改 server.yaml 后重启</small>
          </div>
          <div className="form-group form-readonly">
            <FieldLabel label="CORS 允许来源" />
            <input type="text" value={corsOrigins} readOnly />
          </div>
          <div className="form-group form-readonly">
            <FieldLabel label="CORS 允许方法" />
            <input type="text" value={corsMethods} readOnly />
          </div>
          <div className="form-group form-readonly">
            <FieldLabel label="Swagger 文档" />
            <input type="text" value={config.docs_enabled ? '已开启' : '已关闭'} readOnly />
          </div>
          <div className="form-group form-readonly">
            <FieldLabel label="静态文件目录" />
            <input type="text" value={config.static_dir || '(html/ 目录)'} readOnly />
          </div>
        </div>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ 记忆系统 Tab
// ═════════════════════════════════════════════════════════════
const MemorySettings: React.FC = () => {
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
            <Icons.Brain size={16} /> 短期记忆（对话上下文）

          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="最大消息数" desc="保留的最大对话消息数量" />
            <input type="number" value={Number(shortTerm.max_history) || 50} min={10} max={200}
              onChange={e => updateField('short_term.max_history', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="自动压缩阈值 (bytes)" desc="超过此阈值自动压缩工具结果" />
            <input type="number" value={Number(shortTerm.auto_compact_threshold) || 10000} min={1000} max={100000}
              onChange={e => updateField('short_term.auto_compact_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="保留工具结果数" desc="保留最近 N 次完整工具结果" />
            <input type="number" value={Number(shortTerm.max_tool_results) || 10} min={1} max={50}
              onChange={e => updateField('short_term.max_tool_results', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="结果截断长度" desc="过期结果截断后的字符长度" />
            <input type="number" value={Number(shortTerm.max_tool_result_length) || 500} min={100} max={5000}
              onChange={e => updateField('short_term.max_tool_result_length', parseInt(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">会话笔记压缩</div>
          <label className="toggle-switch">
            <input type="checkbox" checked={!!sessionCompact.enabled} onChange={e => updateField('session_compact.enabled', e.target.checked)} />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{sessionCompact.enabled ? '已开启' : '已关闭'}</span>
          </label>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="Token 阈值" desc="超过此 Token 数触发压缩" />
            <input type="number" value={Number(sessionCompact.token_threshold) || 2000} min={500} max={10000}
              onChange={e => updateField('session_compact.token_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="工具调用阈值" desc="达到此次数触发压缩" />
            <input type="number" value={Number(sessionCompact.tool_call_threshold) || 3} min={1} max={20}
              onChange={e => updateField('session_compact.tool_call_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="保留最近消息数" desc="保留最近 N 条原文消息" />
            <input type="number" value={Number(sessionCompact.keep_recent_messages) || 10} min={3} max={50}
              onChange={e => updateField('session_compact.keep_recent_messages', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="笔记最大长度" desc="生成笔记的最大字符长度" />
            <input type="number" value={Number(sessionCompact.max_notes_length) || 2000} min={500} max={10000}
              onChange={e => updateField('session_compact.max_notes_length', parseInt(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">长期记忆（混合召回）</div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="索引数据库路径" />
            <input type="text" value={longTerm.db_file || 'memory/memory_index.db'}
              onChange={e => updateField('long_term.db_file', e.target.value)} />
          </div>
          <div className="form-group">
            <FieldLabel label="记忆存储目录" />
            <input type="text" value={config.memory_dir || 'memory'}
              onChange={e => updateField('memory_dir', e.target.value)} />
          </div>
          <div className="form-group">
            <FieldLabel label="Hybrid Recall" desc="开启 FTS5 + embedding 混合召回" />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!longTerm.hybrid_enabled} onChange={e => updateField('long_term.hybrid_enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{longTerm.hybrid_enabled ? '已开启' : '已关闭'}</span>
            </label>
          </div>
          <div className="form-group">
            <FieldLabel label="Embedding 维度" />
            <input type="number" value={Number(longTerm.embedding_dimensions) || 96} min={16} max={1024}
              onChange={e => updateField('long_term.embedding_dimensions', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="默认 Schema" />
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
            <FieldLabel label="允许存储敏感信息" desc="默认建议关闭，仅在明确需要时开启" />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!longTerm.allow_sensitive_store} onChange={e => updateField('long_term.allow_sensitive_store', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{longTerm.allow_sensitive_store ? '已开启' : '已关闭'}</span>
            </label>
          </div>
        </div>
      </div>

      <MemoryManagerPanel />

      <div className="form-actions">

        <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
          {saving ? '保存中...' : saved ? '✓ 已保存' : '保存记忆配置'}
        </button>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ 学习配置 Tab
// ═════════════════════════════════════════════════════════════
const LearningSettings: React.FC = () => {
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
            <Icons.BookOpen size={16} /> 学习模块配置
          </div>
          <label className="toggle-switch">
            <input type="checkbox" checked={!!config.enabled} onChange={e => updateField('enabled', e.target.checked)} />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.enabled ? '已开启' : '已关闭'}</span>
          </label>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="策略选择" desc="自动学习由 Bandit 算法选择最优策略" />
            <CustomDropdown
              value={overridePolicy}
              items={[
                { value: '', label: '自动学习（推荐）' },
                { value: 'default', label: 'Default - 平衡策略' },
                { value: 'efficient', label: 'Efficient - 高效快速' },
                { value: 'aggressive', label: 'Aggressive - 激进宽容' },
              ]}
              onChange={val => updateField('override_policy', val || null)}
            />
          </div>
          <div className="form-group">
            <FieldLabel label="统计数据路径" desc="学习统计数据的存储位置" />
            <input type="text" value={config.memory_path || 'data/learning/policy_bandit_stats.json'}
              onChange={e => updateField('memory_path', e.target.value)} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            衰减配置
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="衰减间隔" desc="每 N 个会话执行一次衰减" />
            <input type="number" value={Number(decay.interval) || 50} min={10} max={500}
              onChange={e => updateField('decay.interval', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="衰减因子" desc="衰减系数，越小衰减越快" />
            <input type="number" value={Number(decay.factor) || 0.99} min={0.9} max={1} step={0.01}
              onChange={e => updateField('decay.factor', parseFloat(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            冷启动参数（Beta 分布先验）
          </div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="Alpha" />
            <input type="number" value={Number(prior.alpha) || 2.0} min={0.1} max={10} step={0.1}
              onChange={e => updateField('prior.alpha', parseFloat(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="Beta" />
            <input type="number" value={Number(prior.beta) || 2.0} min={0.1} max={10} step={0.1}
              onChange={e => updateField('prior.beta', parseFloat(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            Policy 策略模板
          </div>
        </div>
        <div className="settings-card-grid">
          {Object.entries(policies).map(([name, params]: [string, any]) => (
            <div key={name} className="policy-group">
              <div className="policy-header">{name}</div>
              <div className="policy-params">
                <div className="form-group">
                  <FieldLabel label="无进展迭代阈值" />
                  <input type="number" value={Number(params.stuck_iterations) || 3} min={1} max={20}
                    onChange={e => updateField(`policies.${name}.stuck_iterations`, parseInt(e.target.value))} />
                </div>
                <div className="form-group">
                  <FieldLabel label="重复调用阈值" />
                  <input type="number" value={Number(params.repetition_threshold) || 3} min={1} max={20}
                    onChange={e => updateField(`policies.${name}.repetition_threshold`, parseInt(e.target.value))} />
                </div>
                <div className="form-group">
                  <FieldLabel label="趋势恶化阈值" />
                  <input type="number" value={Number(params.progress_trend_threshold) || -0.3} min={-1} max={0} step={0.1}
                    onChange={e => updateField(`policies.${name}.progress_trend_threshold`, parseFloat(e.target.value))} />
                </div>
                <div className="form-group">
                  <FieldLabel label="终止确认置信度" />
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
          {saving ? '保存中...' : saved ? '已保存' : '保存学习配置'}
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
    description: '仅在极端情况下终止，适合长时间运行的任务',
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
    description: '在效率和安全之间取得平衡，适合大多数任务',
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
    description: '快速检测问题并终止，节省 Token，适合简单任务',
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
    description: '更严格检测无进展和循环，给 Agent 更多尝试机会',
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
    description: '完全禁用启发式引擎，仅通过迭代次数限制终止',
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
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [selectedPreset, setSelectedPreset] = useState<PresetKey | 'custom'>('balanced');
  const [showAdvanced, setShowAdvanced] = useState(false);
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
    return <div className="settings-panel"><div className="settings-loading"><span className="loading-dots"><span></span><span></span><span></span></span> 加载中...</div></div>;
  }

  return (
    <div className="settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            引擎决策
          </div>
          <button
            className={`btn-link ${showAdvanced ? 'active' : ''}`}
            onClick={() => setShowAdvanced(!showAdvanced)}
            style={{ fontSize: '12px', marginLeft: 'auto' }}
          >
            {showAdvanced ? '▲ 收起高级配置' : '▼ 高级配置'}
          </button>
        </div>
        <div className="preset-grid">
          {(Object.entries(HEURISTICS_PRESETS) as [PresetKey, typeof HEURISTICS_PRESETS[PresetKey]][]).map(([key, preset]) => (
            <div
              key={key}
              className={`preset-card ${selectedPreset === key ? 'selected' : ''}`}
              onClick={() => applyPreset(key)}
            >
              <div className="preset-label">{preset.label}</div>
              <div className="preset-desc">{preset.description}</div>
            </div>
          ))}
        </div>
      </div>

      {showAdvanced && (
        <>
          <div className="settings-card">
            <div className="settings-card-header">
              <div className="settings-card-title">日志与追踪</div>
            </div>
            <div className="settings-card-grid">
              <div className="form-group">
                <FieldLabel label="日志级别" />
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
                <FieldLabel label="决策追踪" />
                <label className="toggle-switch">
                  <input type="checkbox" checked={!!config.trace_enabled} onChange={e => updateField('trace_enabled', e.target.checked)} />
                  <span className="toggle-slider"></span>
                </label>
              </div>
            </div>
          </div>

          <div className="settings-card">
            <div className="settings-card-header">
              <div className="settings-card-title">全局阈值</div>
            </div>
            <div className="settings-card-grid">
              <div className="form-group">
                <FieldLabel label="迭代上限警告比例" desc="迭代次数达到上限的此比例时发出警告" />
                <input type="number" value={Number(thresholds.max_iterations_ratio) || 0.8} min={0.5} max={1} step={0.05}
                  onChange={e => updateField('thresholds.max_iterations_ratio', parseFloat(e.target.value))} />
              </div>
              <div className="form-group">
                <FieldLabel label="Token 预算警告比例" desc="Token 使用达到预算的此比例时发出警告" />
                <input type="number" value={Number(thresholds.token_budget_ratio) || 0.9} min={0.5} max={1} step={0.05}
                  onChange={e => updateField('thresholds.token_budget_ratio', parseFloat(e.target.value))} />
              </div>
              <div className="form-group">
                <FieldLabel label="无进展迭代阈值" desc="连续 N 次迭代无进展则判定为停滞" />
                <input type="number" value={Number(thresholds.stuck_iterations) || 3} min={1} max={20}
                  onChange={e => updateField('thresholds.stuck_iterations', parseInt(e.target.value))} />
              </div>
              <div className="form-group">
                <FieldLabel label="重复调用阈值" desc="同一工具连续调用的最大次数" />
                <input type="number" value={Number(thresholds.repetition_threshold) || 3} min={1} max={20}
                  onChange={e => updateField('thresholds.repetition_threshold', parseInt(e.target.value))} />
              </div>
              <div className="form-group">
                <FieldLabel label="EMA 平滑因子" desc="指数移动平均的平滑系数" />
                <input type="number" value={Number(thresholds.ema_alpha) || 0.3} min={0.1} max={1} step={0.05}
                  onChange={e => updateField('thresholds.ema_alpha', parseFloat(e.target.value))} />
              </div>
              <div className="form-group">
                <FieldLabel label="高原期停滞上限" desc="高原期最大停滞次数" />
                <input type="number" value={Number(thresholds.plateau_stuck_limit) || 5} min={1} max={30}
                  onChange={e => updateField('thresholds.plateau_stuck_limit', parseInt(e.target.value))} />
              </div>
            </div>
          </div>

          <div className="settings-card">
            <div className="settings-card-header">
              <div className="settings-card-title">规则开关</div>
            </div>
            <div className="settings-card-grid">
              <div className="form-group">
                <FieldLabel label="Token 预算保护 (term-002)" />
                <label className="toggle-switch">
                  <input type="checkbox" checked={!!(config.rules?.['term-002']?.enabled)} onChange={e => updateField('rules.term-002.enabled', e.target.checked)} />
                  <span className="toggle-slider"></span>
                </label>
              </div>
              <div className="form-group">
                <FieldLabel label="空结果链检测 (term-003)" />
                <label className="toggle-switch">
                  <input type="checkbox" checked={!!(config.rules?.['term-003']?.enabled)} onChange={e => updateField('rules.term-003.enabled', e.target.checked)} />
                  <span className="toggle-slider"></span>
                </label>
              </div>
              <div className="form-group">
                <FieldLabel label="无进展检测 (term-004)" />
                <label className="toggle-switch">
                  <input type="checkbox" checked={!!(config.rules?.['term-004']?.enabled)} onChange={e => updateField('rules.term-004.enabled', e.target.checked)} />
                  <span className="toggle-slider"></span>
                </label>
              </div>
              <div className="form-group">
                <FieldLabel label="重复工具调用 (loop-001)" />
                <label className="toggle-switch">
                  <input type="checkbox" checked={!!(config.rules?.['loop-001']?.enabled)} onChange={e => updateField('rules.loop-001.enabled', e.target.checked)} />
                  <span className="toggle-slider"></span>
                </label>
              </div>
              <div className="form-group">
                <FieldLabel label="模式循环检测 (loop-002)" />
                <label className="toggle-switch">
                  <input type="checkbox" checked={!!(config.rules?.['loop-002']?.enabled)} onChange={e => updateField('rules.loop-002.enabled', e.target.checked)} />
                  <span className="toggle-slider"></span>
                </label>
              </div>
            </div>
          </div>
        </>
      )}

      <div className="form-actions">
        <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
          {saving ? '保存中...' : saved ? '✓ 已保存' : '保存启发式配置'}
        </button>
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
      case 'server': return <ServerSettings />;
      default: return null;
    }
  };

  return (
    <div className="settings-page">
      {/* 头部 */}
      <div className="settings-header">
        <h2><Icons.Settings size={20} /> 设置</h2>
        <button
          className="btn-back-chat"
          onClick={() => setShowSettingsPage(false)}
          title="返回对话"
        >
          <Icons.Chat size={18} />
          <span>返回对话</span>
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
