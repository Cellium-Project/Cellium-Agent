import React from 'react';

interface NumberInputProps {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  className?: string;
  disabled?: boolean;
}

export const NumberInput: React.FC<NumberInputProps> = ({
  value,
  onChange,
  min,
  max,
  step = 1,
  className = '',
  disabled = false,
}) => {
  const handleIncrement = () => {
    if (disabled) return;
    const newValue = step < 1 
      ? Math.round((value + step) * 100) / 100 
      : value + step;
    if (max !== undefined && newValue > max) return;
    onChange(newValue);
  };

  const handleDecrement = () => {
    if (disabled) return;
    const newValue = step < 1 
      ? Math.round((value - step) * 100) / 100 
      : value - step;
    if (min !== undefined && newValue < min) return;
    onChange(newValue);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    if (val === '') {
      onChange(min ?? 0);
      return;
    }
    const num = step < 1 ? parseFloat(val) : parseInt(val, 10);
    if (isNaN(num)) return;
    
    let clamped = num;
    if (min !== undefined) clamped = Math.max(min, clamped);
    if (max !== undefined) clamped = Math.min(max, clamped);
    
    onChange(clamped);
  };

  return (
    <div className={`number-input-wrapper ${className}`}>
      <input
        type="number"
        value={value}
        onChange={handleChange}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
      />
      <div className="number-input-controls">
        <button
          type="button"
          className="number-input-btn"
          onClick={handleIncrement}
          disabled={disabled || (max !== undefined && value >= max)}
          tabIndex={-1}
        >
          ▲
        </button>
        <button
          type="button"
          className="number-input-btn"
          onClick={handleDecrement}
          disabled={disabled || (min !== undefined && value <= min)}
          tabIndex={-1}
        >
          ▼
        </button>
      </div>
    </div>
  );
};
