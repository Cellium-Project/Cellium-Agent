import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { API, fetchJSON, postJSON, putJSON, deleteJSON } from '../utils/api';
import { Icons } from './Icons';
import { CustomDropdown } from './CustomDropdown';
import { TimePicker } from './TimePicker';
import { useAppStore } from '../stores/appStore';

interface ScheduledTask {
  id: string;
  name: string;
  type: string;
  config: Record<string, any>;
  prompt: string;
  created_at: string;
  next_run: string;
  last_run: string | null;
  run_count: number;
  enabled: boolean;
  session_id: string | null;
}

interface TaskStats {
  total_tasks: number;
  enabled_tasks: number;
  pending_count: number;
  processing_count: number;
  history_count: number;
}

const WeekdaySelector: React.FC<{
  value: number[];
  onChange: (value: number[]) => void;
}> = ({ value, onChange }) => {
  const { t } = useTranslation();
  
  const weekdays = [
    { value: 0, label: t('scheduler.weekdayMon') },
    { value: 1, label: t('scheduler.weekdayTue') },
    { value: 2, label: t('scheduler.weekdayWed') },
    { value: 3, label: t('scheduler.weekdayThu') },
    { value: 4, label: t('scheduler.weekdayFri') },
    { value: 5, label: t('scheduler.weekdaySat') },
    { value: 6, label: t('scheduler.weekdaySun') },
  ];

  const toggleDay = (day: number) => {
    if (value.includes(day)) {
      if (value.length > 1) {
        onChange(value.filter(d => d !== day));
      }
    } else {
      onChange([...value, day].sort());
    }
  };

  return (
    <div className="weekday-selector">
      {weekdays.map((day) => (
        <button
          key={day.value}
          type="button"
          className={`weekday-btn ${value.includes(day.value) ? 'selected' : ''}`}
          onClick={() => toggleDay(day.value)}
        >
          {day.label}
        </button>
      ))}
    </div>
  );
};

export const SchedulerManagerTab: React.FC = () => {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [filterQuery, setFilterQuery] = useState('');
  const [selectedTask, setSelectedTask] = useState<ScheduledTask | null>(null);
  const [saving, setSaving] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [showCreate, setShowCreate] = useState(false);

  const [createForm, setCreateForm] = useState({
    name: '',
    task_type: 'interval',
    prompt: '',
    minutes: 60,
    time: '09:00',
    weekdays: [0] as number[],
    enabled: true,
  });

  const [editForm, setEditForm] = useState({
    name: '',
    prompt: '',
    task_type: 'interval',
    minutes: 60,
    time: '09:00',
    weekdays: [0] as number[],
  });

  const currentSessionId = useAppStore((state) => state.currentSessionId);

  const loadTasks = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const [tasksData, statsData] = await Promise.all([
        fetchJSON<{ items: ScheduledTask[]; total: number }>(API.scheduler),
        fetchJSON<TaskStats>(API.schedulerStats),
      ]);
      setTasks(tasksData.items || []);
      setStats(statsData);
    } catch (e: any) {
      console.error('Failed to load tasks:', e);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    const interval = setInterval(() => {
      loadTasks(false);
    }, 5000);
    return () => clearInterval(interval);
  }, [loadTasks]);

  const filteredTasks = useMemo(() => {
    if (!filterQuery.trim()) return tasks;
    const query = filterQuery.toLowerCase();
    return tasks.filter(
      (task) =>
        task.name.toLowerCase().includes(query) ||
        task.type.toLowerCase().includes(query) ||
        task.prompt.toLowerCase().includes(query)
    );
  }, [tasks, filterQuery]);

  const buildConfig = (taskType: string, form: { minutes: number; time: string; weekdays: number[] }) => {
    let config: Record<string, any> = {};
    if (taskType === 'interval') {
      config = { minutes: form.minutes };
    } else if (taskType === 'daily') {
      const [hour, minute] = form.time.split(':').map(Number);
      config = { hour, minute };
    } else if (taskType === 'weekly') {
      const [hour, minute] = form.time.split(':').map(Number);
      config = { weekdays: form.weekdays, hour, minute };
    }
    return config;
  };

  const handleCreate = async () => {
    if (!createForm.name.trim() || !createForm.prompt.trim()) return;
    
    setSaving(true);
    try {
      const config = buildConfig(createForm.task_type, createForm);
      await postJSON(API.scheduler, {
        name: createForm.name,
        task_type: createForm.task_type,
        prompt: createForm.prompt,
        config,
        enabled: createForm.enabled,
        session_id: currentSessionId,
      });
      
      setCreateForm({ 
        name: '', 
        task_type: 'interval', 
        prompt: '', 
        minutes: 60, 
        time: '09:00', 
        weekdays: [0], 
        enabled: true 
      });
      setShowCreate(false);
      await loadTasks();
    } catch (e: any) {
      console.error('Create failed:', e);
    } finally {
      setSaving(false);
    }
  };

  const handleEdit = (task: ScheduledTask) => {
    setSelectedTask(task);
    setIsEditing(true);
    setShowCreate(false);
    
    const editFormState = {
      name: task.name,
      prompt: task.prompt,
      task_type: task.type,
      minutes: task.config.minutes || 60,
      time: `${String(task.config.hour || 9).padStart(2, '0')}:${String(task.config.minute || 0).padStart(2, '0')}`,
      weekdays: task.config.weekdays || [task.config.weekday || 0],
    };
    setEditForm(editFormState);
  };

  const handleSaveEdit = async () => {
    if (!selectedTask || !editForm.name.trim() || !editForm.prompt.trim()) return;
    
    setSaving(true);
    try {
      const config = buildConfig(editForm.task_type, editForm);
      const updated = await putJSON<ScheduledTask>(API.schedulerDetail(selectedTask.id), {
        name: editForm.name,
        prompt: editForm.prompt,
        config,
      });
      
      setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      setSelectedTask(updated);
      setIsEditing(false);
    } catch (e: any) {
      console.error('Update failed:', e);
    } finally {
      setSaving(false);
    }
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
    if (selectedTask) {
      setEditForm({
        name: selectedTask.name,
        prompt: selectedTask.prompt,
        task_type: selectedTask.type,
        minutes: selectedTask.config.minutes || 60,
        time: `${String(selectedTask.config.hour || 9).padStart(2, '0')}:${String(selectedTask.config.minute || 0).padStart(2, '0')}`,
        weekdays: selectedTask.config.weekdays || [selectedTask.config.weekday || 0],
      });
    }
  };

  const handleToggle = async (task: ScheduledTask) => {
    setSaving(true);
    try {
      const updated = await fetchJSON<ScheduledTask>(API.schedulerToggle(task.id), {
        method: 'PATCH',
      });
      setTasks((prev) =>
        prev.map((t) => (t.id === updated.id ? updated : t))
      );
      if (stats) {
        setStats({
          ...stats,
          enabled_tasks: updated.enabled ? stats.enabled_tasks + 1 : stats.enabled_tasks - 1,
        });
      }
      if (selectedTask?.id === task.id) {
        setSelectedTask(updated);
      }
    } catch (e: any) {
      console.error('Toggle failed:', e);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (task: ScheduledTask) => {
    if (!confirm(t('scheduler.deleteConfirm', { name: task.name }))) return;
    setSaving(true);
    try {
      await deleteJSON<{ success: boolean; deleted_id: string }>(API.schedulerDetail(task.id));
      if (selectedTask?.id === task.id) {
        setSelectedTask(null);
        setIsEditing(false);
      }
      await loadTasks();
    } catch (e: any) {
      console.error('Delete failed:', e);
    } finally {
      setSaving(false);
    }
  };

  const handleSelectTask = (task: ScheduledTask) => {
    setSelectedTask(task);
    setIsEditing(false);
    setShowCreate(false);
  };

  const handleShowCreate = () => {
    setShowCreate(true);
    setSelectedTask(null);
    setIsEditing(false);
    setCreateForm({
      name: '',
      task_type: 'interval',
      prompt: '',
      minutes: 60,
      time: '09:00',
      weekdays: [0],
      enabled: true,
    });
  };

  const formatTime = (isoStr: string | null) => {
    if (!isoStr) return '-';
    try {
      const date = new Date(isoStr);
      return date.toLocaleString();
    } catch {
      return isoStr;
    }
  };

  const getNextRunDisplay = (task: ScheduledTask) => {
    if (!task.enabled) return t('scheduler.disabled');
    try {
      const next = new Date(task.next_run);
      const now = new Date();
      const diff = next.getTime() - now.getTime();
      
      if (diff <= 0) return t('scheduler.pending');
      
      const minutes = Math.floor(diff / 60000);
      if (minutes < 60) return `${t('scheduler.inMinutes', { count: minutes })}`;
      const hours = Math.floor(minutes / 60);
      if (hours < 24) return `${t('scheduler.inHours', { count: hours })}`;
      const days = Math.floor(hours / 24);
      return `${t('scheduler.inDays', { count: days })}`;
    } catch {
      return formatTime(task.next_run);
    }
  };

  const getTypeLabel = (type: string) => {
    switch (type) {
      case 'interval': return t('scheduler.typeInterval');
      case 'daily': return t('scheduler.typeDaily');
      case 'weekly': return t('scheduler.typeWeekly');
      default: return type;
    }
  };

  const getTypeConfigDisplay = (task: ScheduledTask) => {
    switch (task.type) {
      case 'interval':
        return `${task.config.minutes || 60}${t('scheduler.minutes')}`;
      case 'daily':
        return `${String(task.config.hour || 9).padStart(2, '0')}:${String(task.config.minute || 0).padStart(2, '0')}`;
      case 'weekly':
        const weekdaysArr = task.config.weekdays || [task.config.weekday];
        const weekdays = t('scheduler.weekdays', { returnObjects: true }) as string[];
        const weekdayNames = weekdaysArr.map((w: number) => weekdays?.[w] || '').join('、');
        return `${weekdayNames} ${String(task.config.hour || 9).padStart(2, '0')}:${String(task.config.minute || 0).padStart(2, '0')}`;
      default:
        return '';
    }
  };

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'interval': return <Icons.Refresh size={16} />;
      case 'daily': return <Icons.Sun size={16} />;
      case 'weekly': return <Icons.Calendar size={16} />;
      default: return <Icons.Clock size={16} />;
    }
  };

  const taskTypeItems = [
    { value: 'interval', label: t('scheduler.typeInterval') },
    { value: 'daily', label: t('scheduler.typeDaily') },
    { value: 'weekly', label: t('scheduler.typeWeekly') },
  ];

  const enabledTasks = filteredTasks.filter(t => t.enabled);
  const disabledTasks = filteredTasks.filter(t => !t.enabled);

  return (
    <div className="scheduler-layout">
      <div className="scheduler-sidebar">
        <div className="scheduler-sidebar-header">
          <h3>{t('settings.tabs.scheduler')}</h3>
          <div className="scheduler-header-actions">
            <button className="btn-icon" onClick={handleShowCreate} title={t('common.create')}>
              <Icons.Plus size={14} />
            </button>
            <button className="btn-icon" onClick={() => loadTasks()} disabled={saving} title={t('common.refresh')}>
              <Icons.Refresh size={14} />
            </button>
          </div>
        </div>

        {stats && (
          <div className="scheduler-stats-row">
            <div className="scheduler-stat">
              <span className="scheduler-stat-value">{stats.total_tasks}</span>
              <span className="scheduler-stat-label">{t('scheduler.statsTotal')}</span>
            </div>
            <div className="scheduler-stat">
              <span className="scheduler-stat-value enabled">{stats.enabled_tasks}</span>
              <span className="scheduler-stat-label">{t('scheduler.statsEnabled')}</span>
            </div>
            <div className="scheduler-stat">
              <span className="scheduler-stat-value pending">{stats.pending_count}</span>
              <span className="scheduler-stat-label">{t('scheduler.statsPending')}</span>
            </div>
          </div>
        )}

        <div className="scheduler-search">
          <Icons.Search size={14} />
          <input
            type="text"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder={t('scheduler.searchPlaceholder')}
          />
        </div>

        <div className="scheduler-task-list">
          {loading ? (
            <div className="loading-indicator">{t('common.loading')}</div>
          ) : filteredTasks.length === 0 ? (
            <div className="scheduler-empty">{filterQuery ? t('scheduler.noMatch') : t('scheduler.noTasks')}</div>
          ) : (
            <>
              {enabledTasks.length > 0 && (
                <div className="scheduler-task-group">
                  <div className="scheduler-group-header">
                    <Icons.CheckCircle size={12} />
                    <span>{t('common.enabled')} ({enabledTasks.length})</span>
                  </div>
                  {enabledTasks.map((task) => (
                    <div
                      key={task.id}
                      className={`scheduler-task-item ${selectedTask?.id === task.id ? 'selected' : ''}`}
                      onClick={() => handleSelectTask(task)}
                    >
                      <div className="scheduler-task-icon">
                        {getTypeIcon(task.type)}
                      </div>
                      <div className="scheduler-task-info">
                        <div className="scheduler-task-name">{task.name}</div>
                        <div className="scheduler-task-meta">
                          <span className="scheduler-task-type">{getTypeConfigDisplay(task)}</span>
                          <span className="scheduler-task-next">
                            <Icons.Clock size={10} />
                            {getNextRunDisplay(task)}
                          </span>
                        </div>
                      </div>
                      <div className="scheduler-task-badge">
                        {task.run_count > 0 && <span className="run-count">{task.run_count}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {disabledTasks.length > 0 && (
                <div className="scheduler-task-group">
                  <div className="scheduler-group-header disabled">
                    <Icons.Pause size={12} />
                    <span>{t('common.disabled')} ({disabledTasks.length})</span>
                  </div>
                  {disabledTasks.map((task) => (
                    <div
                      key={task.id}
                      className={`scheduler-task-item disabled ${selectedTask?.id === task.id ? 'selected' : ''}`}
                      onClick={() => handleSelectTask(task)}
                    >
                      <div className="scheduler-task-icon">
                        {getTypeIcon(task.type)}
                      </div>
                      <div className="scheduler-task-info">
                        <div className="scheduler-task-name">{task.name}</div>
                        <div className="scheduler-task-meta">
                          <span className="scheduler-task-type">{getTypeConfigDisplay(task)}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <div className="scheduler-main">
        {showCreate ? (
          <div className="scheduler-create">
            <div className="scheduler-create-header">
              <h3>{t('scheduler.createTitle')}</h3>
              <button className="btn-close" onClick={() => setShowCreate(false)}>
                <Icons.Close size={16} />
              </button>
            </div>

            <div className="scheduler-create-form">
              <div className="form-row">
                <div className="form-group">
                  <label>{t('scheduler.taskName')}</label>
                  <input
                    type="text"
                    value={createForm.name}
                    onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                    placeholder={t('scheduler.taskNamePlaceholder')}
                  />
                </div>
                <div className="form-group">
                  <label>{t('scheduler.taskType')}</label>
                  <CustomDropdown
                    value={createForm.task_type}
                    items={taskTypeItems}
                    onChange={(val) => setCreateForm({ ...createForm, task_type: val })}
                  />
                </div>
              </div>

              {createForm.task_type === 'interval' && (
                <div className="form-group">
                  <label>{t('scheduler.intervalMinutes')}</label>
                  <input
                    type="number"
                    value={createForm.minutes}
                    onChange={(e) => setCreateForm({ ...createForm, minutes: parseInt(e.target.value) || 60 })}
                    min="1"
                    max="1440"
                  />
                </div>
              )}

              {createForm.task_type === 'daily' && (
                <div className="form-group">
                  <label>{t('scheduler.executeTime')}</label>
                  <TimePicker
                    value={createForm.time}
                    onChange={(val) => setCreateForm({ ...createForm, time: val })}
                  />
                </div>
              )}

              {createForm.task_type === 'weekly' && (
                <div className="form-group">
                  <label>{t('scheduler.weekday')}</label>
                  <WeekdaySelector
                    value={createForm.weekdays}
                    onChange={(val) => setCreateForm({ ...createForm, weekdays: val })}
                  />
                  <label style={{ marginTop: '12px' }}>{t('scheduler.executeTime')}</label>
                  <TimePicker
                    value={createForm.time}
                    onChange={(val) => setCreateForm({ ...createForm, time: val })}
                  />
                </div>
              )}

              <div className="form-group">
                <label>{t('scheduler.prompt')}</label>
                <textarea
                  value={createForm.prompt}
                  onChange={(e) => setCreateForm({ ...createForm, prompt: e.target.value })}
                  placeholder={t('scheduler.promptPlaceholder')}
                  rows={4}
                />
              </div>

              <div className="form-actions">
                <button
                  className="btn-primary"
                  onClick={handleCreate}
                  disabled={saving || !createForm.name.trim() || !createForm.prompt.trim()}
                >
                  {saving ? t('common.loading') : t('common.create')}
                </button>
              </div>
            </div>
          </div>
        ) : selectedTask ? (
          <div className="scheduler-detail">
            <div className="scheduler-detail-header">
              <div className="scheduler-detail-title">
                <span className={`scheduler-status-dot ${selectedTask.enabled ? 'enabled' : 'disabled'}`} />
                <h3>{isEditing ? editForm.name : selectedTask.name}</h3>
              </div>
              <div className="scheduler-detail-actions">
                {isEditing ? (
                  <>
                    <button
                      className="btn-sm btn-primary"
                      onClick={handleSaveEdit}
                      disabled={saving}
                    >
                      <Icons.Check size={14} />
                      {t('common.save')}
                    </button>
                    <button
                      className="btn-sm btn-secondary"
                      onClick={handleCancelEdit}
                      disabled={saving}
                    >
                      {t('common.cancel')}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className="btn-sm btn-primary"
                      onClick={() => handleEdit(selectedTask)}
                      disabled={saving}
                    >
                      <Icons.Edit2 size={14} />
                      {t('common.edit')}
                    </button>
                    <button
                      className={`btn-sm ${selectedTask.enabled ? 'btn-warning' : 'btn-primary'}`}
                      onClick={() => handleToggle(selectedTask)}
                      disabled={saving}
                    >
                      {selectedTask.enabled ? <Icons.Pause size={14} /> : <Icons.Play size={14} />}
                      {selectedTask.enabled ? t('scheduler.disable') : t('scheduler.enable')}
                    </button>
                    <button
                      className="btn-sm btn-danger"
                      onClick={() => handleDelete(selectedTask)}
                      disabled={saving}
                    >
                      <Icons.Trash size={14} />
                      {t('common.delete')}
                    </button>
                  </>
                )}
              </div>
            </div>

            {!isEditing && (
              <div className="scheduler-detail-grid">
                <div className="scheduler-detail-card">
                  <div className="scheduler-detail-card-icon">
                    <Icons.Activity size={20} />
                  </div>
                  <div className="scheduler-detail-card-content">
                    <span className="scheduler-detail-card-value">{selectedTask.run_count}</span>
                    <span className="scheduler-detail-card-label">{t('scheduler.runCount')}</span>
                  </div>
                </div>

                <div className="scheduler-detail-card">
                  <div className="scheduler-detail-card-icon next">
                    <Icons.Clock size={20} />
                  </div>
                  <div className="scheduler-detail-card-content">
                    <span className="scheduler-detail-card-value">{getNextRunDisplay(selectedTask)}</span>
                    <span className="scheduler-detail-card-label">{t('scheduler.nextRun')}</span>
                  </div>
                </div>

                <div className="scheduler-detail-card">
                  <div className="scheduler-detail-card-icon last">
                    <Icons.History size={20} />
                  </div>
                  <div className="scheduler-detail-card-content">
                    <span className="scheduler-detail-card-value">{formatTime(selectedTask.last_run)}</span>
                    <span className="scheduler-detail-card-label">{t('scheduler.lastRun')}</span>
                  </div>
                </div>

                <div className="scheduler-detail-card">
                  <div className="scheduler-detail-card-icon created">
                    <Icons.Calendar size={20} />
                  </div>
                  <div className="scheduler-detail-card-content">
                    <span className="scheduler-detail-card-value">{formatTime(selectedTask.created_at)}</span>
                    <span className="scheduler-detail-card-label">{t('scheduler.createdAt')}</span>
                  </div>
                </div>
              </div>
            )}

            {isEditing ? (
              <div className="scheduler-edit-form">
                <div className="form-row">
                  <div className="form-group">
                    <label>{t('scheduler.taskName')}</label>
                    <input
                      type="text"
                      value={editForm.name}
                      onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                      placeholder={t('scheduler.taskNamePlaceholder')}
                    />
                  </div>
                  <div className="form-group">
                    <label>{t('scheduler.taskType')}</label>
                    <CustomDropdown
                      value={editForm.task_type}
                      items={taskTypeItems}
                      onChange={(val) => setEditForm({ ...editForm, task_type: val })}
                    />
                  </div>
                </div>

                {editForm.task_type === 'interval' && (
                  <div className="form-group">
                    <label>{t('scheduler.intervalMinutes')}</label>
                    <input
                      type="number"
                      value={editForm.minutes}
                      onChange={(e) => setEditForm({ ...editForm, minutes: parseInt(e.target.value) || 60 })}
                      min="1"
                      max="1440"
                    />
                  </div>
                )}

                {editForm.task_type === 'daily' && (
                  <div className="form-group">
                    <label>{t('scheduler.executeTime')}</label>
                    <TimePicker
                      value={editForm.time}
                      onChange={(val) => setEditForm({ ...editForm, time: val })}
                    />
                  </div>
                )}

                {editForm.task_type === 'weekly' && (
                  <div className="form-group">
                    <label>{t('scheduler.weekday')}</label>
                    <WeekdaySelector
                      value={editForm.weekdays}
                      onChange={(val) => setEditForm({ ...editForm, weekdays: val })}
                    />
                    <label style={{ marginTop: '12px' }}>{t('scheduler.executeTime')}</label>
                    <TimePicker
                      value={editForm.time}
                      onChange={(val) => setEditForm({ ...editForm, time: val })}
                    />
                  </div>
                )}

                <div className="form-group">
                  <label>{t('scheduler.prompt')}</label>
                  <textarea
                    value={editForm.prompt}
                    onChange={(e) => setEditForm({ ...editForm, prompt: e.target.value })}
                    placeholder={t('scheduler.promptPlaceholder')}
                    rows={4}
                  />
                </div>
              </div>
            ) : (
              <>
                <div className="scheduler-detail-section">
                  <div className="scheduler-detail-section-header">
                    <Icons.Settings size={14} />
                    <span>{t('scheduler.config')}</span>
                  </div>
                  <div className="scheduler-detail-section-content">
                    <div className="scheduler-config-item">
                      <span className="scheduler-config-label">{t('scheduler.taskType')}</span>
                      <span className="scheduler-config-value">
                        {getTypeIcon(selectedTask.type)}
                        {getTypeLabel(selectedTask.type)}
                      </span>
                    </div>
                    <div className="scheduler-config-item">
                      <span className="scheduler-config-label">{t('scheduler.executeTime')}</span>
                      <span className="scheduler-config-value">{getTypeConfigDisplay(selectedTask)}</span>
                    </div>
                  </div>
                </div>

                <div className="scheduler-detail-section">
                  <div className="scheduler-detail-section-header">
                    <Icons.FileText size={14} />
                    <span>{t('scheduler.prompt')}</span>
                  </div>
                  <div className="scheduler-detail-section-content">
                    <pre className="scheduler-prompt-preview">{selectedTask.prompt}</pre>
                  </div>
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="scheduler-empty-main">
            <div className="scheduler-empty-content">
              <Icons.Clock size={48} />
              <h4>{t('scheduler.selectOrCreate')}</h4>
              <p>{t('scheduler.selectOrCreateDesc')}</p>
              <button className="btn-primary" onClick={handleShowCreate}>
                <Icons.Plus size={14} />
                {t('scheduler.createTask')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
