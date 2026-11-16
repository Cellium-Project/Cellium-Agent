import React from 'react';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: React.ReactNode;
  icon?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  busy?: boolean;
  busyLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open, title, message, icon, confirmLabel = '确定', cancelLabel = '取消',
  danger = false, busy = false, busyLabel = '', onConfirm, onCancel,
}) => {
  if (!open) return null;
  return (
    <div className="confirm-overlay" onClick={busy ? undefined : onCancel} role="presentation">
      <div
        className="confirm-card"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="confirm-head">
          {icon && <span className="confirm-icon">{icon}</span>}
          <span className="confirm-title">{title}</span>
        </div>
        <div className="confirm-body">{message}</div>
        <div className="confirm-actions">
          <button className="confirm-btn confirm-btn-cancel" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            className={`confirm-btn ${danger ? 'confirm-btn-danger' : 'confirm-btn-primary'}`}
            onClick={onConfirm}
            disabled={busy}
            autoFocus
          >
            {busy && busyLabel ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};
