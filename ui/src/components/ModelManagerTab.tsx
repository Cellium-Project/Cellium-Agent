import React, { useState, useEffect, useCallback } from 'react';
import { API, fetchJSON, postJSON } from '../utils/api';
import type { ModelConfig } from '../types';

const LOCAL_PRESETS = {
  lmstudio: { name: 'LM Studio', base_url: 'http://localhost:1234/v1', provider: 'local' as const },
  ollama: { name: 'Ollama', base_url: 'http://localhost:11434/v1', provider: 'local' as const },
  vllm: { name: 'vLLM', base_url: 'http://localhost:8000/v1', provider: 'local' as const },
};

interface BackendModel {
  name: string;
  provider: string;
  base_url: string;
  model: string;
  api_key?: string;
  temperature: number;
  timeout: number;
}

export const ModelManagerTab: React.FC = () => {
  const [savedModels, setSavedModels] = useState<ModelConfig[]>([]);
  const [currentModelId, setCurrentModelId] = useState<string | null>(null);
  const [editingModel, setEditingModel] = useState<ModelConfig | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [name, setName] = useState('');
  const [provider, setProvider] = useState<'openai' | 'local'>('openai');
  const [baseUrl, setBaseUrl] = useState('');
  const [modelId, setModelId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [temperature, setTemperature] = useState(0.7);
  const [timeout, setTimeout_] = useState(120);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);

  const loadModels = useCallback(async () => {
    try {
      const data = await fetchJSON<{ models: BackendModel[] }>(API.modelList);
      const models: ModelConfig[] = data.models?.map((m, idx) => ({
        id: m.name,
        name: m.name,
        provider: m.provider as 'openai' | 'local',
        base_url: m.base_url,
        model: m.model,
        api_key: m.api_key || '',
        temperature: m.temperature,
        timeout: m.timeout,
      })) || [];
      setSavedModels(models);
      if (models.length > 0 && !currentModelId) {
        setCurrentModelId(models[0].id);
      }
    } catch (error) {
      console.error('加载模型列表失败:', error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadModels();
  }, [loadModels]);

  const resetForm = () => {
    setEditingModel(null);
    setName('');
    setProvider('openai');
    setBaseUrl('');
    setModelId('');
    setApiKey('');
    setTemperature(0.7);
    setTimeout_(120);
    setAvailableModels([]);
  };

  const handleSave = async () => {
    if (!name || !baseUrl || !modelId) {
      alert('请填写必要字段');
      return;
    }

    const model: BackendModel = {
      name,
      provider,
      base_url: baseUrl,
      model: modelId,
      api_key: apiKey,
      temperature,
      timeout,
    };

    try {
      await postJSON(API.modelAdd, model);
      await loadModels();
      resetForm();
      alert(`模型已保存: ${name}`);
    } catch (error: any) {
      alert(`保存失败: ${error.message}`);
    }
  };

  const handleSwitch = async (model: ModelConfig) => {
    try {
      await postJSON(API.modelSwitch, {
        name: model.name,
        provider: model.provider,
        base_url: model.base_url,
        model: model.model,
        api_key: model.api_key || '',
        temperature: model.temperature,
        timeout: model.timeout,
      });

      await postJSON(API.modelReloadEngine, {});

      setCurrentModelId(model.id);
      alert(`已切换到: ${model.name}`);
    } catch (error: any) {
      alert(`切换失败: ${error.message}`);
    }
  };

  const handleEdit = (model: ModelConfig) => {
    setEditingModel(model);
    setName(model.name);
    setProvider(model.provider);
    setBaseUrl(model.base_url);
    setModelId(model.model);
    setApiKey(model.api_key || '');
    setTemperature(model.temperature);
    setTimeout_(model.timeout);
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`确定删除模型 "${name}"？`)) return;
    try {
      await fetchJSON(API.modelDelete(name), { method: 'DELETE' });
      await loadModels();
      if (currentModelId === name) {
        setCurrentModelId(null);
      }
    } catch (error: any) {
      alert(`删除失败: ${error.message}`);
    }
  };

  const handleFetchModels = async () => {
    if (!baseUrl) {
      alert('请先输入 API 地址');
      return;
    }

    setIsLoadingModels(true);
    try {
      const data = await postJSON<{ models: { id?: string }[] }>(
        API.modelListLocal,
        { base_url: baseUrl }
      );
      setAvailableModels(data.models?.map((m) => m.id || String(m)) || []);
    } catch (error: any) {
      alert(`获取模型列表失败: ${error.message}`);
    } finally {
      setIsLoadingModels(false);
    }
  };

  const handleQuickAdd = (preset: keyof typeof LOCAL_PRESETS) => {
    const p = LOCAL_PRESETS[preset];
    setName(p.name);
    setProvider(p.provider);
    setBaseUrl(p.base_url);
    handleFetchModels();
  };

  if (isLoading) {
    return <div className="settings-panel">加载中...</div>;
  }

  return (
    <div className="settings-panel">
      <h3 className="settings-section-title">模型管理</h3>
      <p className="settings-desc">管理并快速切换您常用的 API 服务和本地部署模型。</p>

      <div className="model-manager">
        <div className="saved-models">
          <h4>已保存的模型</h4>
          <div className="model-list">
            {savedModels.length === 0 ? (
              <div className="model-list-empty">暂无保存的模型</div>
            ) : (
              savedModels.map((model) => (
                <div
                  key={model.id}
                  className={`model-item ${model.id === currentModelId ? 'active' : ''}`}
                >
                  <span
                    className="model-item-name"
                    onClick={() => handleSwitch(model)}
                  >
                    {model.name}
                  </span>
                  <div className="model-item-actions">
                    <button
                      className="model-item-btn"
                      onClick={() => handleEdit(model)}
                      title="编辑"
                    >
                      ✏️
                    </button>
                    <button
                      className="model-item-btn"
                      onClick={() => handleDelete(model.name)}
                      title="删除"
                    >
                      🗑️
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="model-form">
          <h4>{editingModel ? '编辑模型' : '添加新模型'}</h4>

          <div className="form-group">
            <label>配置名称 *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如: 我的 LM Studio"
            />
          </div>

          <div className="form-group">
            <label>Provider</label>
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value as 'openai' | 'local')}
            >
              <option value="openai">OpenAI 兼容</option>
              <option value="local">本地部署</option>
            </select>
          </div>

          <div className="form-group">
            <label>API 地址 *</label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="例如: http://localhost:1234/v1"
            />
          </div>

          <div className="form-group">
            <label>模型 ID *</label>
            {provider === 'local' && availableModels.length > 0 ? (
              <select
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
              >
                <option value="">-- 选择模型 --</option>
                {availableModels.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
                placeholder="例如: gpt-4o"
              />
            )}
          </div>

          <div className="form-group">
            <label>API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="可选，本地部署可留空"
            />
          </div>

          <div className="form-row">
            <div className="form-group">
              <label>Temperature</label>
              <input
                type="number"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
                min={0}
                max={2}
                step={0.1}
              />
            </div>
            <div className="form-group">
              <label>超时 (秒)</label>
              <input
                type="number"
                value={timeout}
                onChange={(e) => setTimeout_(parseInt(e.target.value))}
                min={10}
                max={600}
              />
            </div>
          </div>

          <div className="form-actions">
            <button className="btn-primary" onClick={handleSave}>
              {editingModel ? '更新模型' : '保存模型'}
            </button>
            {editingModel && (
              <button className="btn-secondary" onClick={resetForm}>
                取消编辑
              </button>
            )}
          </div>

          <div className="quick-presets">
            <span className="quick-presets-label">快速添加:</span>
            <button onClick={() => handleQuickAdd('lmstudio')}>LM Studio</button>
            <button onClick={() => handleQuickAdd('ollama')}>Ollama</button>
            <button onClick={() => handleQuickAdd('vllm')}>vLLM</button>
          </div>
        </div>
      </div>
    </div>
  );
};