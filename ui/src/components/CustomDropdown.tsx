import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { createPortal } from 'react-dom';

interface DropdownItem {
  value: string;
  label: string;
}

interface CustomDropdownProps {
  value: string;
  items: DropdownItem[];
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

export const CustomDropdown: React.FC<CustomDropdownProps> = ({
  value,
  items,
  onChange,
  placeholder,
  disabled = false,
  className = '',
}) => {
  const { t } = useTranslation();
  const defaultPlaceholder = placeholder || t('common.pleaseSelect');
  const [isOpen, setIsOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0, width: 0 });
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const selectedItem = items.find(item => item.value === value);
  const displayText = selectedItem?.label || defaultPlaceholder;

  const updatePosition = useCallback(() => {
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setMenuPosition({
        top: rect.bottom + 4,
        left: rect.left,
        width: rect.width,
      });
    }
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      const inDropdown = dropdownRef.current?.contains(target);
      const inMenu = menuRef.current?.contains(target);
      if (!inDropdown && !inMenu) {
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

  const handleSelect = (itemValue: string) => {
    onChange(itemValue);
    setIsOpen(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsOpen(false);
    }
  };

  const handleToggle = () => {
    if (!disabled) {
      if (!isOpen) {
        updatePosition();
      }
      setIsOpen(!isOpen);
    }
  };

  const menuElement = isOpen && (
    <div
      ref={menuRef}
      className="dropdown-menu show"
      role="listbox"
      style={{
        position: 'fixed',
        top: menuPosition.top,
        left: menuPosition.left,
        width: menuPosition.width,
        zIndex: 99999,
      }}
    >
      {items.map((item) => (
        <div
          key={item.value}
          className={`dropdown-item ${item.value === value ? 'selected' : ''}`}
          onClick={() => handleSelect(item.value)}
          role="option"
          aria-selected={item.value === value}
        >
          {item.label}
        </div>
      ))}
    </div>
  );

  return (
    <div className={`custom-dropdown ${className}`} ref={dropdownRef} onKeyDown={handleKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        className={`dropdown-trigger ${isOpen ? 'open' : ''} ${disabled ? 'disabled' : ''}`}
        onClick={handleToggle}
        disabled={disabled}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
      >
        {displayText}
      </button>
      {menuElement && createPortal(menuElement, document.body)}
    </div>
  );
};