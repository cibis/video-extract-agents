/**
 * GeneratedFilesPanel — compact chip list of files produced by completed jobs.
 *
 * Rendered inside JobStatusBridge (which mounts at the LibreChat app root).
 * Uses position:fixed so it floats above the conversation regardless of scroll.
 * Each chip shows a file-type icon, filename, and a direct download link.
 */
import React from 'react';

export interface OutputFile {
  id: string;
  filename: string | null;
  content_type: string;
  signed_url: string;
}

function fileIcon(contentType: string): string {
  if (contentType.startsWith('video/')) return '🎬';
  if (contentType.startsWith('image/')) return '🖼️';
  if (contentType.includes('json')) return '📋';
  if (contentType.includes('csv')) return '📊';
  return '📄';
}

interface Props {
  outputs: OutputFile[];
  onDismiss: () => void;
}

export const GeneratedFilesPanel: React.FC<Props> = ({ outputs, onDismiss }) => {
  if (outputs.length === 0) return null;

  return (
    <div
      style={{
        position: 'fixed',
        bottom: '80px',
        right: '16px',
        zIndex: 1000,
        background: '#ffffff',
        border: '1px solid #e5e7eb',
        borderRadius: '8px',
        padding: '10px 12px',
        boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
        maxWidth: '300px',
        minWidth: '200px',
        fontSize: '0.8rem',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '8px',
        }}
      >
        <span
          style={{
            fontWeight: 600,
            color: '#374151',
            fontSize: '0.7rem',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          Generated files
        </span>
        <button
          onClick={onDismiss}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: '#9ca3af',
            fontSize: '0.85rem',
            lineHeight: 1,
            padding: '0 2px',
          }}
          aria-label="Dismiss"
        >
          ✕
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {outputs.map((f) => (
          <a
            key={f.id}
            href={f.signed_url}
            download={f.filename ?? undefined}
            target="_blank"
            rel="noreferrer"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '5px 8px',
              background: '#f3f4f6',
              borderRadius: '5px',
              color: '#1d4ed8',
              textDecoration: 'none',
              overflow: 'hidden',
            }}
          >
            <span style={{ flexShrink: 0 }}>{fileIcon(f.content_type)}</span>
            <span
              style={{
                flex: 1,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontSize: '0.78rem',
              }}
            >
              {f.filename ?? 'output'}
            </span>
            <span style={{ color: '#6b7280', flexShrink: 0, fontSize: '0.75rem' }}>↓</span>
          </a>
        ))}
      </div>
    </div>
  );
};

export default GeneratedFilesPanel;
