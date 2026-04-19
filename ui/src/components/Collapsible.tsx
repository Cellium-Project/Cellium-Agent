import React, { useState, useEffect, useRef } from 'react';

interface CollapsibleProps {
  summary: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  open?: boolean;
  className?: string;
}

export const Collapsible: React.FC<CollapsibleProps> = ({
  summary,
  children,
  defaultOpen = false,
  open,
  className = ''
}) => {
  const [isOpenInternal, setIsOpenInternal] = useState(defaultOpen);
  const contentRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | undefined>(defaultOpen ? undefined : 0);
  
  const isOpen = open !== undefined ? open : isOpenInternal;
  const setIsOpen = open !== undefined ? (() => {}) : setIsOpenInternal;

  useEffect(() => {
    const contentEl = contentRef.current;
    if (!contentEl) return;

    if (isOpen) {
      setHeight(contentEl.scrollHeight);
      const timer = setTimeout(() => setHeight(undefined), 200);
      return () => clearTimeout(timer);
    } else {
      setHeight(contentEl.scrollHeight);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setHeight(0));
      });
    }
  }, [isOpen]);

  const handleClick = () => {
    if (open === undefined) {
      setIsOpenInternal(!isOpenInternal);
    }
  };

  return (
    <div className={`collapsible ${className}`}>
      <div className="collapsible-summary" onClick={handleClick}>
        <span className={`collapsible-arrow ${isOpen ? 'open' : ''}`}>▶</span>
        {summary}
      </div>
      <div
        className="collapsible-content"
        style={{ height: height !== undefined ? `${height}px` : 'auto' }}
      >
        <div ref={contentRef}>{children}</div>
      </div>
    </div>
  );
};
