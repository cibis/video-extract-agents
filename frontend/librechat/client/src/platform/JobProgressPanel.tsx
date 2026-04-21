/**
 * JobProgressPanel — displays live agent step events during job execution.
 * Rendered by JobStatusBridge when a job is running; hidden on completion.
 */
import React, { useEffect, useRef } from 'react';

export interface ProgressStep {
  stepName: string;
  stepStatus: string;
}

interface JobProgressPanelProps {
  steps: ProgressStep[];
  onDismiss: () => void;
}

export const JobProgressPanel: React.FC<JobProgressPanelProps> = ({ steps, onDismiss }) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to latest step
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [steps.length]);

  if (steps.length === 0) return null;

  return (
    <div style={{
      position: 'fixed',
      bottom: '80px',
      right: '16px',
      width: '320px',
      maxHeight: '240px',
      background: 'var(--surface-secondary, #1e1e2e)',
      border: '1px solid var(--border-medium, #3f3f5a)',
      borderRadius: '8px',
      boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
      display: 'flex',
      flexDirection: 'column',
      zIndex: 1000,
      fontFamily: 'inherit',
      fontSize: '13px',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '8px 12px',
        borderBottom: '1px solid var(--border-medium, #3f3f5a)',
        color: 'var(--text-secondary, #a0a0b8)',
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{
            width: '8px', height: '8px', borderRadius: '50%',
            background: '#4ade80',
            boxShadow: '0 0 6px #4ade80',
            animation: 'pulse 1.5s ease-in-out infinite',
            display: 'inline-block',
          }} />
          Agent running…
        </span>
        <button
          onClick={onDismiss}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--text-secondary, #a0a0b8)', fontSize: '16px', lineHeight: 1,
            padding: '0 2px',
          }}
          aria-label="Dismiss progress panel"
        >
          ×
        </button>
      </div>

      {/* Step list */}
      <div style={{ overflowY: 'auto', padding: '8px 12px', flex: 1 }}>
        {steps.map((step, i) => (
          <div key={i} style={{
            padding: '3px 0',
            color: i === steps.length - 1
              ? 'var(--text-primary, #e0e0f0)'
              : 'var(--text-secondary, #a0a0b8)',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '6px',
          }}>
            <span style={{ marginTop: '2px', flexShrink: 0 }}>
              {i === steps.length - 1 ? '▶' : '✓'}
            </span>
            <span>{step.stepName}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
};

export default JobProgressPanel;
