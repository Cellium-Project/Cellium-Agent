import React, { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

interface TimePickerProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
}

export const TimePicker: React.FC<TimePickerProps> = ({
  value,
  onChange,
  disabled = false,
  className = '',
}) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const [hour, minute] = value.split(':').map(Number);

  const hours = Array.from({ length: 24 }, (_, i) => i);
  const minutes = Array.from({ length: 60 }, (_, i) => i);

  const updatePosition = useCallback(() => {
    if (inputRef.current) {
      const rect = inputRef.current.getBoundingClientRect();
      const viewportHeight = window.innerHeight;
      const spaceBelow = viewportHeight - rect.bottom;
      const menuHeight = 320;
      
      let top: number;
      if (spaceBelow >= menuHeight) {
        top = rect.bottom + 4;
      } else {
        top = rect.top - menuHeight - 4;
      }
      
      setMenuPosition({
        top,
        left: rect.left,
      });
    }
  }, []);

  const formatTime = (h: number, m: number): string => {
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    const cleaned = raw.replace(/[^\d]/g, '');
    
    let h = 0;
    let m = 0;
    
    if (cleaned.length === 0) {
      onChange('00:00');
      return;
    }
    
    if (cleaned.length <= 2) {
      h = parseInt(cleaned, 10);
      if (h > 23) h = 23;
      m = 0;
    } else {
      h = parseInt(cleaned.slice(0, 2), 10);
      if (h > 23) h = 23;
      m = parseInt(cleaned.slice(2, 4), 10);
      if (m > 59) m = 59;
    }
    
    onChange(formatTime(h, m));
  };

  const handleInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsOpen(false);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setIsOpen(true);
    }
  };

  const handleInputFocus = () => {
    if (!disabled) {
      updatePosition();
      setIsOpen(true);
    }
  };

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      const inInput = inputRef.current?.contains(target);
      const inMenu = menuRef.current?.contains(target);
      if (!inInput && !inMenu) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (isOpen) {
      updatePosition();
      const handleScroll = () => updatePosition();
      const handleResize = () => updatePosition();
      window.addEventListener('scroll', handleScroll, true);
      window.addEventListener('resize', handleResize);
      return () => {
        window.removeEventListener('scroll', handleScroll, true);
        window.removeEventListener('resize', handleResize);
      };
    }
  }, [isOpen, updatePosition]);

  const handleSelectHour = (h: number) => {
    onChange(formatTime(h, minute));
  };

  const handleSelectMinute = (m: number) => {
    onChange(formatTime(hour, m));
    setIsOpen(false);
  };

  const menuElement = isOpen && (
    <div
      ref={menuRef}
      className="time-picker-menu"
      style={{
        position: 'fixed',
        top: menuPosition.top,
        left: menuPosition.left,
        zIndex: 99999,
      }}
    >
      <div className="time-picker-columns">
        <div className="time-picker-column">
          <div className="time-picker-header">{t('scheduler.hour')}</div>
          <div className="time-picker-list">
            {hours.map((h) => (
              <button
                key={h}
                type="button"
                className={`time-picker-item ${h === hour ? 'selected' : ''}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  handleSelectHour(h);
                }}
              >
                {String(h).padStart(2, '0')}
              </button>
            ))}
          </div>
        </div>
        <div className="time-picker-column">
          <div className="time-picker-header">{t('scheduler.minute')}</div>
          <div className="time-picker-list">
            {minutes.map((m) => (
              <button
                key={m}
                type="button"
                className={`time-picker-item ${m === minute ? 'selected' : ''}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  handleSelectMinute(m);
                }}
              >
                {String(m).padStart(2, '0')}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );

  return (
    <div className={`time-picker ${className}`}>
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={handleInputChange}
        onFocus={handleInputFocus}
        onKeyDown={handleInputKeyDown}
        disabled={disabled}
        className={`time-picker-input ${isOpen ? 'open' : ''} ${disabled ? 'disabled' : ''}`}
        placeholder="HH:MM"
        maxLength={5}
      />
      {menuElement && createPortal(menuElement, document.body)}
    </div>
  );
};
