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
  { id: 'logging', label: '日志设置', Icon: Icons.FileText },
  { id: 'server', label: '服务端 & 网络', Icon: Icons.Globe },
] as const;

// ── 通用组件：字段标签 ─────────────────────────────────────
const FieldLabel: React.FC<{ label: string; tooltip?: string }> = ({ label, tooltip }) => (
  <span className="settings-field-label" title={tooltip || ''}>
    {label}
  </span>
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
// ★ 模型配置 Tab
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
        <div className="settings-loading"><span className="loading"></span> 加载中...</div>
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
            <FieldLabel label="限制迭代次数" tooltip="开启后将强制限制 Agent 的最大迭代次数，达到上限后自动终止" />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!config.enforce_iteration_limit} onChange={e => updateField('enforce_iteration_limit', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{config.enforce_iteration_limit ? '已启用限制' : '无限制'}</span>
            </label>
          </div>
          {config.enforce_iteration_limit && (
            <div className="form-group">
              <FieldLabel label="最大迭代次数" tooltip="Agent 执行的最大迭代次数上限" />
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
            <FieldLabel label="Flash 模式" tooltip="开启后跳过窗口上下文注入，减少 token 消耗" />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!config.flash_mode} onChange={e => updateField('flash_mode', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{config.flash_mode ? '已开启' : '已关闭'}</span>
            </label>
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

  const apiKey = config.api_key || {};

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

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            API Key 认证
          </div>
          <label className="toggle-switch">
            <input type="checkbox" checked={!!apiKey.enabled} onChange={e => updateField('api_key.enabled', e.target.checked)} />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{apiKey.enabled ? '已开启' : '已关闭'}</span>
          </label>
        </div>
        {apiKey.enabled && (
          <div className="settings-card-grid">
            <div className="form-group">
              <FieldLabel label="API Key" />
              <input type="password" value={apiKey.key || ''} onChange={e => updateField('api_key.key', e.target.value)} placeholder="输入密钥..." />
            </div>
            <div className="form-group">
              <FieldLabel label="认证请求头" />
              <input type="text" value={apiKey.header_name || 'X-API-Key'} onChange={e => updateField('api_key.header_name', e.target.value)} />
            </div>
          </div>
        )}
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
// ★ 日志设置 Tab
// ═════════════════════════════════════════════════════════════
const LoggingSettings: React.FC = () => {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const { saving, saved, doSave } = useSavingState();

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('logging')).then(data => {
      setConfig(data); setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

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
            <FieldLabel label="最大消息数" tooltip="保留的最大对话消息数量" />
            <input type="number" value={Number(shortTerm.max_history) || 50} min={10} max={200}
              onChange={e => updateField('short_term.max_history', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="自动压缩阈值 (bytes)" tooltip="超过此阈值自动压缩工具结果" />
            <input type="number" value={Number(shortTerm.auto_compact_threshold) || 10000} min={1000} max={100000}
              onChange={e => updateField('short_term.auto_compact_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="保留工具结果数" tooltip="保留最近 N 次完整工具结果" />
            <input type="number" value={Number(shortTerm.max_tool_results) || 10} min={1} max={50}
              onChange={e => updateField('short_term.max_tool_results', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="结果截断长度" tooltip="过期结果截断后的字符长度" />
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
            <FieldLabel label="Token 阈值" tooltip="超过此 Token 数触发压缩" />
            <input type="number" value={Number(sessionCompact.token_threshold) || 2000} min={500} max={10000}
              onChange={e => updateField('session_compact.token_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="工具调用阈值" tooltip="达到此次数触发压缩" />
            <input type="number" value={Number(sessionCompact.tool_call_threshold) || 3} min={1} max={20}
              onChange={e => updateField('session_compact.tool_call_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="保留最近消息数" tooltip="保留最近 N 条原文消息" />
            <input type="number" value={Number(sessionCompact.keep_recent_messages) || 10} min={3} max={50}
              onChange={e => updateField('session_compact.keep_recent_messages', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="笔记最大长度" tooltip="生成笔记的最大字符长度" />
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
            <FieldLabel label="Hybrid Recall" tooltip="开启 FTS5 + embedding 混合召回" />
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
            <FieldLabel label="允许存储敏感信息" tooltip="默认建议关闭，仅在明确需要时开启" />
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
            <FieldLabel label="统计数据路径" tooltip="学习统计数据的存储位置" />
            <input type="text" value={config.memory_path || 'data/learning/policy_bandit_stats.json'}
              onChange={e => updateField('memory_path', e.target.value)} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">衰减配置</div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="衰减间隔 (会话数)" tooltip="每 N 个会话执行一次衰减" />
            <input type="number" value={Number(decay.interval) || 50} min={10} max={500}
              onChange={e => updateField('decay.interval', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="衰减因子" tooltip="衰减系数，越小衰减越快" />
            <input type="number" value={Number(decay.factor) || 0.99} min={0.9} max={1} step={0.01}
              onChange={e => updateField('decay.factor', parseFloat(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">冷启动参数（Beta 分布先验）</div>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="Alpha" tooltip="Beta 分布先验 alpha 参数" />
            <input type="number" value={Number(prior.alpha) || 2.0} min={0.1} max={10} step={0.1}
              onChange={e => updateField('prior.alpha', parseFloat(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="Beta" tooltip="Beta 分布先验 beta 参数" />
            <input type="number" value={Number(prior.beta) || 2.0} min={0.1} max={10} step={0.1}
              onChange={e => updateField('prior.beta', parseFloat(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="form-actions">
        <button className={`btn-primary ${saving ? 'saving' : ''} ${saved ? 'saved' : ''}`} onClick={handleSave} disabled={saving}>
          {saving ? '保存中...' : saved ? '✓ 已保存' : '保存学习配置'}
        </button>
      </div>
    </div>
  );
};

// ═════════════════════════════════════════════════════════════
// ★ 启发式引擎 Tab
// ═════════════════════════════════════════════════════════════
const HeuristicsSettings: React.FC = () => {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const { saving, saved, doSave } = useSavingState();

  useEffect(() => {
    fetchJSON<Record<string, any>>(API.configSection('heuristics')).then(data => {
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
    await putJSON(API.configUpdate('heuristics'), { value: config, persist: true });
  });

  const thresholds = config.thresholds || {};

  return (
    <div className="settings-panel">
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <Icons.Lightbulb size={16} /> 启发式引擎
          </div>
          <label className="toggle-switch">
            <input type="checkbox" checked={!!config.enabled} onChange={e => updateField('enabled', e.target.checked)} />
            <span className="toggle-slider"></span>
            <span className="toggle-label">{config.enabled ? '已开启' : '已关闭'}</span>
          </label>
        </div>
        <div className="settings-card-grid">
          <div className="form-group">
            <FieldLabel label="日志级别" />
            <CustomDropdown
              value={(config.log_level || 'info').toUpperCase()}
              items={[
                { value: 'DEBUG', label: 'DEBUG — 调试' },
                { value: 'INFO', label: 'INFO — 信息' },
                { value: 'WARNING', label: 'WARNING — 警告' },
                { value: 'ERROR', label: 'ERROR — 错误' },
              ]}
              onChange={val => updateField('log_level', val.toLowerCase())}
            />
          </div>
          <div className="form-group">
            <FieldLabel label="决策追踪" tooltip="记录每次决策的详细过程" />
            <label className="toggle-switch">
              <input type="checkbox" checked={!!config.trace_enabled} onChange={e => updateField('trace_enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
              <span className="toggle-label">{config.trace_enabled ? '已开启' : '已关闭'}</span>
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
            <FieldLabel label="迭代上限警告比例" tooltip="迭代次数达到上限的此比例时发出警告" />
            <input type="number" value={Number(thresholds.max_iterations_ratio) || 0.8} min={0.5} max={1} step={0.05}
              onChange={e => updateField('thresholds.max_iterations_ratio', parseFloat(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="Token 预算警告比例" tooltip="Token 使用达到预算的此比例时发出警告" />
            <input type="number" value={Number(thresholds.token_budget_ratio) || 0.9} min={0.5} max={1} step={0.05}
              onChange={e => updateField('thresholds.token_budget_ratio', parseFloat(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="无进展迭代阈值" tooltip="连续 N 次迭代无进展则判定为停滞" />
            <input type="number" value={Number(thresholds.stuck_iterations) || 3} min={1} max={10}
              onChange={e => updateField('thresholds.stuck_iterations', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="重复调用阈值" tooltip="同一工具连续调用的最大次数" />
            <input type="number" value={Number(thresholds.repetition_threshold) || 3} min={1} max={10}
              onChange={e => updateField('thresholds.repetition_threshold', parseInt(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="EMA 平滑因子" tooltip="指数移动平均的平滑系数" />
            <input type="number" value={Number(thresholds.ema_alpha) || 0.3} min={0.1} max={1} step={0.1}
              onChange={e => updateField('thresholds.ema_alpha', parseFloat(e.target.value))} />
          </div>
          <div className="form-group">
            <FieldLabel label="高原期停滞上限" tooltip="高原期最大停滞次数" />
            <input type="number" value={Number(thresholds.plateau_stuck_limit) || 5} min={1} max={20}
              onChange={e => updateField('thresholds.plateau_stuck_limit', parseInt(e.target.value))} />
          </div>
        </div>
      </div>

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
