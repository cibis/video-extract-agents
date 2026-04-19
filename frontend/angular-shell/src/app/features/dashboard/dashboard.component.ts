import { Component, DestroyRef, OnInit, OnDestroy, inject, signal, computed } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DatePipe } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Subscription } from 'rxjs';
import { finalize, forkJoin, of } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { ApiService, Job, Output, SessionAsset, JobLog } from '../../core/services/api.service';
import { JobService, JobStreamEvent, ToolProgressData } from '../../core/services/job.service';
import { AuthService } from '../../core/auth/auth.service';
import { environment } from '../../../environments/environment';

interface JobFiles {
  outputs: Output[];
  inputs: SessionAsset[];
}

interface SessionGroup {
  sessionId: string | null;
  label: string;
  date: string;
  jobs: Job[];
  isTest: boolean;
  isFromFile?: boolean;
}

interface TextDialog {
  label: string;
  text: string;
  formatted: string;
  isJson: boolean;
  copied: boolean;
}

/** Deep-walk a parsed JSON value and try to parse any string values that look
 *  like JSON objects or arrays. Falls back to the original string on failure. */
function deepParseJsonStrings(val: unknown): unknown {
  if (typeof val === 'string') {
    const trimmed = val.trim();
    if ((trimmed.startsWith('{') || trimmed.startsWith('[')) && trimmed.length > 1) {
      try {
        const inner = JSON.parse(trimmed);
        return deepParseJsonStrings(inner);
      } catch {
        return val;
      }
    }
    return val;
  }
  if (Array.isArray(val)) {
    return val.map(deepParseJsonStrings);
  }
  if (val !== null && typeof val === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
      out[k] = deepParseJsonStrings(v);
    }
    return out;
  }
  return val;
}

/** Replace literal \n and \t escape sequences with real whitespace for display.
 *  Handles both \\n (double-escaped, e.g. from Python repr) and \n (single-escaped).
 *  The double-escaped form is matched first to avoid leaving stray backslashes.
 *  Falls back to the original string if anything goes wrong. */
function unescapeForDisplay(s: string): string {
  try {
    return s
      .replace(/\\\\n/g, '\n')
      .replace(/\\n/g, '\n')
      .replace(/\\\\t/g, '\t')
      .replace(/\\t/g, '\t')
      .replace(/\\"/g, '"');
  } catch {
    return s;
  }
}

/** Try to format a string as pretty JSON. Attempts nested JSON-string expansion.
 *  Literal \n sequences in values are converted to real newlines for readability.
 *  Falls back to the original text on any error.
 *  Returns { formatted, isJson }. */
function formatForDialog(text: string): { formatted: string; isJson: boolean } {
  try {
    const parsed = JSON.parse(text);
    try {
      const deep = deepParseJsonStrings(parsed);
      return { formatted: unescapeForDisplay(JSON.stringify(deep, null, 2)), isJson: true };
    } catch {
      return { formatted: unescapeForDisplay(JSON.stringify(parsed, null, 2)), isJson: true };
    }
  } catch {
    return { formatted: unescapeForDisplay(text), isJson: false };
  }
}

const LOG_PALETTE = [
  '#EEF2FF', // soft indigo
  '#FFF7ED', // soft orange
  '#F0FDF4', // soft green
  '#FDF2F8', // soft pink
  '#FFFBEB', // soft amber
  '#F0F9FF', // soft sky
  '#FFF1F2', // soft rose
  '#F5F3FF', // soft violet
];

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [DatePipe],
  template: `
    <div class="dashboard">
      @if (auth.skipAuthMode()) {
        <div class="auth-banner auth-banner--dev">
          Dev mode — auth bypassed. Requests use the static dev identity.
        </div>
      }
      <div class="dashboard__header">
        <h1>Session History</h1>
        <div class="history-controls">
          <div class="history-filter">
            <span class="filter-label">Show:</span>
            <button class="filter-btn" [class.filter-btn--active]="historyFilter() === 'real'" (click)="setFilter('real')">Real</button>
            <button class="filter-btn" [class.filter-btn--active]="historyFilter() === 'test'" (click)="setFilter('test')">Test</button>
            <button class="filter-btn" [class.filter-btn--active]="historyFilter() === 'all'"  (click)="setFilter('all')">All</button>
          </div>
          <div class="wipe-actions">
            <button class="btn--danger" [disabled]="wipeInProgress()" (click)="confirmWipeTestData()">Wipe test data</button>
            <button class="btn--danger btn--danger-all" [disabled]="wipeInProgress()" (click)="confirmWipeAllData()">Wipe all data</button>
          </div>
          <div class="file-load-actions">
            <input #fileInput type="file" multiple accept=".log" style="display:none" (change)="onFilesSelected($event)">
            <button class="btn--file" (click)="fileInput.click()">Load log files</button>
            @if (fileSessionGroups().length > 0) {
              <button class="btn--clear-files" (click)="clearFileGroups()">Clear ({{ fileSessionGroups().length }})</button>
            }
          </div>
        </div>
        @if (wipeResult()) {
          <p class="wipe-result">{{ wipeResult() }}</p>
        }
      </div>
      @if (loading()) {
        <p>Loading jobs...</p>
      }
      @if (filteredSessionGroups().length === 0 && !loading()) {
        <p>No jobs yet. Upload a video and describe what to extract.</p>
      }
      @for (group of filteredSessionGroups(); track group.sessionId) {
        <div class="session-group">
          <div class="session-group__header">
            <span class="session-group__label">{{ group.isFromFile ? group.label : 'Session …' + group.label }}</span>
            @if (group.isFromFile) {
              <span class="session-group__file-badge">from file</span>
            }
            <span class="session-group__date">{{ group.date | date:'mediumDate' }}</span>
          </div>

          <div class="dashboard__jobs">
            @for (job of group.jobs; track job.id) {
              <div class="job-card">
                <div class="job-card__header">
                  <span class="badge badge--{{ job.status }}">{{ job.status }}</span>
                  @let counts = modelCallCounts().get(job.id);
                  @if (counts && counts.size > 0) {
                    <div class="job-card__model-counts">
                      @for (entry of modelCountEntries(counts); track entry.model) {
                        <span class="model-count-chip" [title]="entry.model">{{ shortModelName(entry.model) }} ×{{ entry.count }}</span>
                      }
                    </div>
                  }
                  <time>{{ job.created_at | date:'medium' }}</time>
                </div>
                <p class="job-card__prompt">{{ job.prompt }}</p>

                @if (job.status === 'failed' && job.error) {
                  <p class="job-card__error">Error: {{ job.error }}</p>
                }

                @let files = jobFiles().get(job.id);
                @if (files) {
                  @if (files.outputs.length > 0) {
                    <div class="job-card__files">
                      <p class="job-card__files-label">Generated files</p>
                      @for (output of files.outputs; track output.id) {
                        <div class="job-card__file-row">
                          <a [href]="output.signed_url" target="_blank" class="job-card__download">
                            {{ output.filename ?? 'output' }}
                          </a>
                          <time class="job-card__file-time">{{ output.created_at | date:'short' }}</time>
                        </div>
                      }
                    </div>
                  }
                  @if (files.inputs.length > 0) {
                    <div class="job-card__files">
                      <p class="job-card__files-label">Input files</p>
                      @for (asset of files.inputs; track asset.id) {
                        <div class="job-card__file-row">
                          <a [href]="asset.signed_url" target="_blank" class="job-card__download job-card__download--input">
                            {{ asset.filename ?? asset.asset_type }}
                          </a>
                          <time class="job-card__file-time">{{ asset.created_at | date:'short' }}</time>
                        </div>
                      }
                    </div>
                  }
                }

                <!-- Per-job activity log -->
                <div class="job-logs-bar">
                  <button
                    class="logs-toggle"
                    (click)="toggleJobLogs(job.id)"
                    [attr.aria-expanded]="expandedJob() === job.id">
                    {{ expandedJob() === job.id ? 'Hide activity log' : 'Show activity log' }}
                  </button>
                </div>

                @if (expandedJob() === job.id) {
                  @let logs = jobLogs().get(job.id);
                  @if (logs === undefined) {
                    <p class="logs-loading">Loading…</p>
                  } @else if (logs.length === 0) {
                    <p class="logs-empty">No log entries recorded for this job.</p>
                  } @else {
                    <div class="job-logs">
                      <table class="logs-table">
                        <thead>
                          <tr>
                            <th>Time</th>
                            <th>Seq</th>
                            <th>Group</th>
                            <th>Service</th>
                            <th>Type</th>
                            <th>Msg</th>
                            <th>Model / Tool</th>
                            <th>Agent</th>
                            <th>Task</th>
                            <th>Len</th>
                            <th>Message</th>
                          </tr>
                        </thead>
                        <tbody>
                          @for (log of logs; track log.id) {
                            @let progressMap = toolProgressMap().get(job.id);
                            @let tp = (log.message_type === 'Input' && log.log_type === 'tool_call' && progressMap)
                                      ? progressMap.get(log.call_group_id) : undefined;
                            <tr [style.background-color]="groupColor(log.call_group_id, logs)"
                                [class.log-row--error]="log.message_type === 'Error'"
                                [class.log-row--selected]="selectedLogId() === log.id"
                                (click)="selectLog(log.id)">
                              <td class="col-time">{{ log.created_at | date:'HH:mm:ss.SSS' }}</td>
                              <td class="col-seq">{{ log.sequence_num }}</td>
                              <td class="col-group" [title]="log.call_group_id">…{{ log.call_group_id.slice(-6) }}</td>
                              <td class="col-service">{{ log.service_name }}</td>
                              <td class="col-type">
                                <span class="type-badge type-badge--{{ log.log_type }}">{{ log.log_type }}</span>
                              </td>
                              <td class="col-msgtype">
                                <span class="msg-badge msg-badge--{{ log.message_type.toLowerCase() }}">{{ log.message_type }}</span>
                                @if (tp) {
                                  @let pct = progressPct(tp);
                                  <div class="tool-progress">
                                    <div class="tool-progress__bar-track">
                                      <div class="tool-progress__bar-fill"
                                           [style.width.%]="pct ?? 40"
                                           [class.tool-progress__bar-fill--indeterminate]="pct === null">
                                      </div>
                                    </div>
                                    <span class="tool-progress__label">
                                      {{ tp.processed_units }}{{ tp.total_units !== null ? '/' + tp.total_units : '' }} {{ tp.unit_label }}
                                    </span>
                                  </div>
                                }
                              </td>
                              <td class="col-model">{{ log.model_id ?? log.tool_name ?? '—' }}</td>
                              <td class="col-agent">{{ log.agent_role ?? '—' }}</td>
                              <td class="col-task">{{ log.task_name ?? '—' }}</td>
                              <td class="col-len">{{ (log.message?.length ?? log.error_text?.length) || null }}</td>
                              <td class="col-data">
                                @if (log.message) {
                                  <span class="data-preview data-preview--clickable"
                                    (click)="openDialog(log.message_type + ' — ' + (log.tool_name ?? log.model_id ?? log.log_type), log.message)">
                                    {{ truncate(log.message) }}
                                  </span>
                                } @else if (log.error_text) {
                                  <span class="data-preview data-preview--error data-preview--clickable"
                                    (click)="openDialog('Error', log.error_text)">
                                    {{ truncate(log.error_text, 80) }}
                                  </span>
                                } @else {
                                  <span class="data-preview">—</span>
                                }
                              </td>
                            </tr>
                          }
                        </tbody>
                      </table>
                    </div>
                  }
                }
              </div>
            }
          </div>
        </div>
      }
    </div>

    @if (dialog()) {
      @let d = dialog()!;
      <div class="dialog-backdrop" (click)="closeDialog()">
        <div class="dialog" (click)="$event.stopPropagation()">
          <div class="dialog__header">
            <span class="dialog__title">{{ d.label }}</span>
            <div class="dialog__actions">
              <button class="dialog__btn dialog__btn--copy" (click)="copyDialog()" [class.dialog__btn--copied]="d.copied">
                {{ d.copied ? 'Copied!' : 'Copy' }}
              </button>
              @if (d.isJson) {
                <span class="dialog__badge">JSON</span>
              }
              <button class="dialog__btn dialog__btn--close" (click)="closeDialog()">✕</button>
            </div>
          </div>
          <pre class="dialog__body" [innerHTML]="dialogSafeHtml()"></pre>
        </div>
      </div>
    }
  `,
  styles: [`
    .auth-banner {
      padding: 0.55rem 1rem;
      border-radius: 6px;
      font-size: 0.85rem;
      font-weight: 500;
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 1rem;
    }
    .auth-banner--dev { background: #fffbeb; border: 1px solid #f59e0b; color: #92400e; }
    .auth-banner--signin { background: #eff6ff; border: 1px solid #3b82f6; color: #1e40af; }
    .auth-banner__btn {
      padding: 0.25rem 0.75rem;
      background: #1e40af;
      color: white;
      border: none;
      border-radius: 4px;
      font-size: 0.8rem;
      cursor: pointer;
    }
    .auth-banner__btn:hover { background: #1d3a9e; }
    .dashboard { width: 100%; }
    .dashboard__header { margin-bottom: 0.5rem; }
    .dashboard__header h1 { margin-bottom: 0.6rem; }
    .history-controls {
      display: flex;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
      margin-bottom: 0.5rem;
    }
    .history-filter { display: flex; align-items: center; gap: 0.35rem; }
    .filter-label { font-size: 0.8rem; color: #555; }
    .filter-btn {
      font-size: 0.78rem;
      padding: 0.2rem 0.75rem;
      border: 1px solid #b0c8e0;
      border-radius: 14px;
      background: transparent;
      color: #0050a0;
      cursor: pointer;
    }
    .filter-btn:hover { background: #deedf8; }
    .filter-btn--active { background: #0050a0; color: white; border-color: #0050a0; }
    .wipe-actions { display: flex; gap: 0.5rem; }
    .btn--danger {
      font-size: 0.78rem;
      padding: 0.2rem 0.75rem;
      border: 1px solid #c62828;
      border-radius: 4px;
      background: transparent;
      color: #c62828;
      cursor: pointer;
    }
    .btn--danger:hover:not([disabled]) { background: #c62828; color: white; }
    .btn--danger[disabled] { opacity: 0.5; cursor: default; }
    .btn--danger-all { border-color: #8b0000; color: #8b0000; }
    .btn--danger-all:hover:not([disabled]) { background: #8b0000; color: white; }
    .wipe-result { font-size: 0.8rem; color: #1a6a1a; margin: 0.2rem 0 0; }
    .session-group {
      margin-top: 1.5rem;
      background: #f3f9ff;
      border: 1px solid #c7e0f4;
      border-radius: 8px;
      overflow: hidden;
    }
    .session-group__header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.45rem 0.85rem;
      background: #deedf8;
      border-bottom: 1px solid #c7e0f4;
    }
    .session-group__label { font-size: 0.75rem; font-weight: 600; font-family: monospace; color: #0050a0; }
    .session-group__date { font-size: 0.75rem; color: #5a7fa0; }
    .dashboard__jobs { display: flex; flex-direction: column; gap: 1rem; padding: 0.85rem; }
    .job-card {
      background: white;
      border-radius: 8px;
      padding: 1.25rem;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    .job-card__header {
      display: flex;
      justify-content: space-between;
      margin-bottom: 0.75rem;
      time { font-size: 0.8rem; color: #666; }
    }
    .job-card__prompt { color: #333; margin: 0 0 0.75rem; }
    .job-card__files { margin-top: 0.75rem; }
    .job-card__files-label { font-size: 0.75rem; color: #888; margin: 0 0 0.4rem; text-transform: uppercase; letter-spacing: 0.04em; }
    .job-card__file-row { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.35rem; }
    .job-card__file-time { font-size: 0.75rem; color: #888; white-space: nowrap; }
    .job-card__download {
      display: inline-block;
      padding: 0.35rem 0.85rem;
      background: #0078d4;
      color: white;
      border-radius: 4px;
      font-size: 0.875rem;
      text-decoration: none;
      max-width: 360px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .job-card__download--input { background: #5a5a5a; }
    .job-card__error { color: #c62828; font-size: 0.875rem; }

    /* ── Activity log toggle ── */
    .job-logs-bar {
      margin-top: 1rem;
      border-top: 1px solid #e4eff8;
      padding-top: 0.6rem;
    }
    .logs-toggle {
      font-size: 0.75rem;
      padding: 0.2rem 0.65rem;
      border: 1px solid #0050a0;
      border-radius: 4px;
      background: transparent;
      color: #0050a0;
      cursor: pointer;
    }
    .logs-toggle:hover { background: #0050a0; color: white; }
    .logs-loading, .logs-empty {
      font-size: 0.8rem;
      color: #888;
      margin: 0.5rem 0 0;
      font-style: italic;
    }

    /* ── Logs table ── */
    .job-logs {
      margin-top: 0.6rem;
      overflow-x: auto;
      border: 1px solid #c7e0f4;
      border-radius: 6px;
    }
    .logs-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.76rem;
    }
    .logs-table th {
      text-align: left;
      padding: 0.4rem 0.55rem;
      background: #e8f2fb;
      border-bottom: 1px solid #c7e0f4;
      font-weight: 600;
      color: #0050a0;
      white-space: nowrap;
    }
    .logs-table td {
      padding: 0.32rem 0.55rem;
      border-bottom: 1px solid #e8f2fb;
      vertical-align: top;
    }
    .logs-table tr:last-child td { border-bottom: none; }
    .log-row--error td { outline: 1px solid #f5c6c6; }
    .log-row--selected td { background-color: rgba(0, 80, 160, 0.10) !important; }
    .log-row--selected td:first-child { border-left: 3px solid #0050a0; }
    .logs-table tr { cursor: pointer; }
    .col-time { white-space: nowrap; color: #666; font-family: monospace; font-size: 0.72rem; }
    .col-seq { white-space: nowrap; color: #999; font-family: monospace; font-size: 0.7rem; text-align: right; padding-right: 0.6rem; }
    .col-group { white-space: nowrap; font-family: monospace; font-size: 0.68rem; color: #888; letter-spacing: 0.02em; }
    .col-service { white-space: nowrap; font-family: monospace; color: #444; font-size: 0.72rem; }
    .col-type { white-space: nowrap; }
    .col-msgtype { white-space: nowrap; }
    .col-model { white-space: nowrap; font-family: monospace; color: #0050a0; font-size: 0.72rem; max-width: 160px; overflow: hidden; text-overflow: ellipsis; }
    .col-agent { white-space: nowrap; font-size: 0.72rem; color: #5a1a9a; max-width: 160px; overflow: hidden; text-overflow: ellipsis; }
    .col-task { white-space: nowrap; font-size: 0.72rem; color: #7a5800; }
    .col-len { white-space: nowrap; font-family: monospace; font-size: 0.68rem; color: #999; text-align: right; padding-right: 0.4rem; }
    .col-data { max-width: 260px; }
    .job-card__model-counts { display: flex; flex-wrap: wrap; gap: 0.3rem; flex: 1; margin: 0 0.5rem; }
    .model-count-chip {
      font-size: 0.67rem;
      font-family: monospace;
      padding: 0.1rem 0.5rem;
      border-radius: 10px;
      background: #e8f2fb;
      color: #0050a0;
      border: 1px solid #c7e0f4;
      white-space: nowrap;
    }
    .type-badge {
      display: inline-block;
      font-size: 0.67rem;
      font-family: monospace;
      border-radius: 10px;
      padding: 0.1rem 0.45rem;
      background: #e4eff8;
      color: #0050a0;
      white-space: nowrap;
    }
    .type-badge--llm_call { background: #dff0e8; color: #1a7a44; }
    .type-badge--tool_call { background: #e8eeff; color: #3050b0; }
    .type-badge--agent_step { background: #fff3cd; color: #7a5800; }
    .type-badge--task_complete { background: #f0e8ff; color: #5a1a9a; }
    .type-badge--error { background: #fde8e8; color: #c62828; }
    .msg-badge {
      display: inline-block;
      font-size: 0.67rem;
      font-family: monospace;
      border-radius: 10px;
      padding: 0.1rem 0.45rem;
      white-space: nowrap;
      font-weight: 600;
    }
    .msg-badge--input  { background: #dbeafe; color: #1d4ed8; }
    .msg-badge--output { background: #dcfce7; color: #15803d; }
    .msg-badge--error  { background: #fde8e8; color: #c62828; }
    .data-preview {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: #555;
    }
    .data-preview--clickable {
      cursor: pointer;
      color: #0050a0;
      text-decoration: underline;
      text-decoration-style: dotted;
      text-underline-offset: 2px;
    }
    .data-preview--clickable:hover { color: #003070; }
    .data-preview--error { color: #c62828; }

    /* ── Text detail dialog ── */
    .dialog-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.45);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 1.5rem;
    }
    .dialog {
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.22);
      max-width: 90vw;
      width: 90vw;
      max-height: 82vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .dialog__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0.65rem 1rem;
      background: #e8f2fb;
      border-bottom: 1px solid #c7e0f4;
      gap: 0.6rem;
      flex-shrink: 0;
    }
    .dialog__title { font-size: 0.85rem; font-weight: 600; color: #0050a0; }
    .dialog__actions { display: flex; align-items: center; gap: 0.5rem; }
    .dialog__badge {
      font-size: 0.68rem;
      font-family: monospace;
      background: #0050a0;
      color: white;
      border-radius: 10px;
      padding: 0.1rem 0.45rem;
    }
    .dialog__btn {
      font-size: 0.75rem;
      padding: 0.2rem 0.6rem;
      border-radius: 4px;
      cursor: pointer;
      border: 1px solid #c7e0f4;
      background: white;
      color: #0050a0;
      white-space: nowrap;
    }
    .dialog__btn:hover { background: #deedf8; }
    .dialog__btn--copy { border-color: #0050a0; }
    .dialog__btn--copied { background: #0050a0; color: white; border-color: #0050a0; }
    .dialog__btn--close { color: #666; font-size: 0.9rem; padding: 0.1rem 0.4rem; }
    .dialog__body {
      padding: 1rem;
      margin: 0;
      overflow: auto;
      flex: 1;
      font-family: monospace;
      font-size: 0.8rem;
      line-height: 1.55;
      color: #222;
      white-space: pre-wrap;
      word-break: break-word;
      background: #fafcff;
    }
    .dialog__link {
      color: #0050a0;
      text-decoration: underline;
      word-break: break-all;
    }
    .dialog__link:hover { color: #003070; }
    /* ── Tool progress bar ── */
    .tool-progress { margin-top: 0.25rem; display: flex; flex-direction: column; gap: 0.15rem; min-width: 90px; }
    .tool-progress__bar-track { height: 5px; background: #dbeafe; border-radius: 3px; overflow: hidden; }
    .tool-progress__bar-fill { height: 100%; background: #1d4ed8; border-radius: 3px; transition: width 0.4s ease; }
    .tool-progress__bar-fill--indeterminate { width: 40% !important; animation: progress-indeterminate 1.2s ease-in-out infinite; }
    @keyframes progress-indeterminate { 0% { transform: translateX(-100%); } 100% { transform: translateX(350%); } }
    .tool-progress__label { font-size: 0.62rem; font-family: monospace; color: #1d4ed8; white-space: nowrap; }

    /* ── File load controls ── */
    .file-load-actions { display: flex; align-items: center; gap: 0.35rem; }
    .btn--file {
      font-size: 0.78rem;
      padding: 0.2rem 0.75rem;
      border: 1px solid #0050a0;
      border-radius: 4px;
      background: transparent;
      color: #0050a0;
      cursor: pointer;
    }
    .btn--file:hover { background: #deedf8; }
    .btn--clear-files {
      font-size: 0.78rem;
      padding: 0.2rem 0.6rem;
      border: 1px solid #888;
      border-radius: 4px;
      background: transparent;
      color: #555;
      cursor: pointer;
    }
    .btn--clear-files:hover { background: #efefef; color: #333; }
    .session-group__file-badge {
      font-size: 0.65rem;
      padding: 0.1rem 0.45rem;
      background: #f0e8ff;
      color: #5a1a9a;
      border-radius: 10px;
      font-family: monospace;
      border: 1px solid #d0c0f0;
      flex-shrink: 0;
    }
  `],
})
export class DashboardComponent implements OnInit, OnDestroy {
  auth = inject(AuthService);
  private api = inject(ApiService);
  private jobService = inject(JobService);
  private destroyRef = inject(DestroyRef);
  private sanitizer = inject(DomSanitizer);

  jobs = signal<Job[]>([]);
  jobFiles = signal<Map<string, JobFiles>>(new Map());
  jobLogs = signal<Map<string, JobLog[]>>(new Map());
  expandedJob = signal<string | null>(null);
  selectedLogId = signal<string | null>(null);
  loading = signal(false);
  dialog = signal<TextDialog | null>(null);
  dialogSafeHtml = computed<SafeHtml>(() => {
    const d = this.dialog();
    return d ? this.renderWithLinks(d.formatted) : this.sanitizer.bypassSecurityTrustHtml('');
  });
  historyFilter = signal<'real' | 'test' | 'all'>('real');
  wipeInProgress = signal(false);
  wipeResult = signal<string | null>(null);

  /** Maps jobId → (call_group_id → ToolProgressData) for active tool calls. */
  toolProgressMap = signal<Map<string, Map<string, ToolProgressData>>>(new Map());

  /** Session groups loaded from local log files — ephemeral, cleared on page refresh. */
  fileSessionGroups = signal<SessionGroup[]>([]);

  /** Job IDs that came from local files — skip API fetch for these. */
  private readonly _fileJobIds = new Set<string>();

  /** Active SSE subscriptions for live-log streaming, keyed by jobId. */
  private readonly _liveLogStreams = new Map<string, Subscription>();

  private readonly _groupColorCache = new Map<string, string>();
  private _groupColorIndex = 0;

  /** Returns the background color for a call_group_id, assigning one from the
   *  palette on first encounter and cycling when all 8 are used. The logs array
   *  is passed so the cache is scoped per render (Angular change detection will
   *  call this with the same array reference, so the map stays stable). */
  groupColor(callGroupId: string, _logs: JobLog[]): string {
    if (!this._groupColorCache.has(callGroupId)) {
      this._groupColorCache.set(
        callGroupId,
        LOG_PALETTE[this._groupColorIndex % LOG_PALETTE.length]
      );
      this._groupColorIndex++;
    }
    return this._groupColorCache.get(callGroupId)!;
  }

  /** Maps jobId → (modelId → llm_call count) derived from loaded logs. */
  modelCallCounts = computed(() => {
    const result = new Map<string, Map<string, number>>();
    for (const [jobId, logs] of this.jobLogs()) {
      const counts = new Map<string, number>();
      for (const log of logs) {
        if (log.log_type === 'llm_call' && log.model_id) {
          counts.set(log.model_id, (counts.get(log.model_id) ?? 0) + 1);
        }
      }
      if (counts.size > 0) result.set(jobId, counts);
    }
    return result;
  });

  modelCountEntries(counts: Map<string, number>): { model: string; count: number }[] {
    return Array.from(counts.entries()).map(([model, count]) => ({ model, count }));
  }

  /** Strip provider prefix (e.g. "anthropic/") for compact chip display. */
  shortModelName(model: string): string {
    const slash = model.lastIndexOf('/');
    return slash >= 0 ? model.slice(slash + 1) : model;
  }

  sessionGroups = computed((): SessionGroup[] => {
    const seen = new Map<string, SessionGroup>();
    const order: string[] = [];
    for (const job of this.jobs()) {
      if (!job.prompt) continue;
      const key = job.session_id ?? 'no-session';
      if (!seen.has(key)) {
        seen.set(key, {
          sessionId: job.session_id,
          label: job.session_id ? job.session_id.slice(-8) : '—',
          date: job.created_at,
          jobs: [],
          isTest: job.is_test ?? false,
        });
        order.push(key);
      }
      seen.get(key)!.jobs.push(job);
    }
    return order.map(k => seen.get(k)!);
  });

  filteredSessionGroups = computed((): SessionGroup[] => {
    const filter = this.historyFilter();
    const regular = this.sessionGroups().filter(group => {
      if (filter === 'real') return !group.isTest;
      if (filter === 'test') return group.isTest;
      return true;
    });
    // File-loaded groups always shown at top regardless of filter
    return [...this.fileSessionGroups(), ...regular];
  });

  ngOnInit(): void {
    this.loadJobs();
    this.jobService.jobCompleted$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.loadJobs());
  }

  setFilter(filter: 'real' | 'test' | 'all'): void {
    this.historyFilter.set(filter);
    this.loadJobs();
  }

  confirmWipeTestData(): void {
    if (!confirm('Delete all test sessions, jobs, and blobs? This cannot be undone.')) return;
    this.wipeInProgress.set(true);
    this.wipeResult.set(null);
    this.api.wipeTestData().subscribe({
      next: ({ sessionsDeleted, blobsDeleted }) => {
        this.wipeResult.set(`Deleted ${sessionsDeleted} test sessions and ${blobsDeleted} blobs.`);
        this.wipeInProgress.set(false);
        this.loadJobs();
      },
      error: () => {
        this.wipeResult.set('Wipe failed — check console.');
        this.wipeInProgress.set(false);
      },
    });
  }

  confirmWipeAllData(): void {
    if (!confirm('Delete ALL sessions, jobs, and blobs for your account? This cannot be undone.')) return;
    this.wipeInProgress.set(true);
    this.wipeResult.set(null);
    this.api.wipeAllData().subscribe({
      next: ({ sessionsDeleted, blobsDeleted }) => {
        this.wipeResult.set(`Deleted ${sessionsDeleted} sessions and ${blobsDeleted} blobs.`);
        this.wipeInProgress.set(false);
        this.loadJobs();
      },
      error: () => {
        this.wipeResult.set('Wipe failed — check console.');
        this.wipeInProgress.set(false);
      },
    });
  }

  toggleJobLogs(jobId: string): void {
    if (this.expandedJob() === jobId) {
      this.expandedJob.set(null);
      this._stopLiveLogStream(jobId);
      return;
    }
    this.expandedJob.set(jobId);

    // File-loaded jobs have logs pre-populated — no API call needed
    if (this._fileJobIds.has(jobId)) return;

    const job = this.jobs().find(j => j.id === jobId);
    const isRunning = job?.status === 'queued' || job?.status === 'processing';

    if (isRunning) {
      // Seed with any logs already in DB, then stream live additions
      this.fetchJobLogs(jobId);
      this._startLiveLogStream(jobId);
    } else {
      // Completed/failed: one-shot fetch (no cache check — always refresh)
      this.fetchJobLogs(jobId);
    }
  }

  ngOnDestroy(): void {
    for (const jobId of this._liveLogStreams.keys()) {
      this._stopLiveLogStream(jobId);
    }
  }

  private _startLiveLogStream(jobId: string): void {
    this._stopLiveLogStream(jobId); // prevent duplicate streams
    const sub = this.jobService.streamJobProgress(jobId).subscribe({
      next: (event: JobStreamEvent) => {
        if (event.type === 'log') {
          this._mergeLiveLog(jobId, event.log);
          // Clear progress bar when the Output or Error row arrives for this tool call
          if (event.log.message_type === 'Output' || event.log.message_type === 'Error') {
            this._clearToolProgress(jobId, event.log.call_group_id);
          }
        } else if (event.type === 'tool_progress') {
          this._mergeToolProgress(jobId, event.toolProgress);
        } else if (event.type === 'status' &&
                   (event.status === 'completed' || event.status === 'failed')) {
          // Job finished: stop streaming and do a final fetch to catch any stragglers
          this._stopLiveLogStream(jobId);
          this.fetchJobLogs(jobId);
        }
      },
      error: () => this._stopLiveLogStream(jobId),
      complete: () => this._stopLiveLogStream(jobId),
    });
    this._liveLogStreams.set(jobId, sub);
  }

  private _stopLiveLogStream(jobId: string): void {
    this._liveLogStreams.get(jobId)?.unsubscribe();
    this._liveLogStreams.delete(jobId);
  }

  /** Merge a single incoming log entry into the sorted buffer for jobId. */
  private _mergeLiveLog(jobId: string, log: JobLog): void {
    const current = this.jobLogs().get(jobId) ?? [];
    // Skip duplicate (same sequence_num already present)
    if (current.some(l => l.sequence_num === log.sequence_num)) return;
    // Insert in sequence_num order
    const updated = [...current, log].sort((a, b) =>
      (a.sequence_num as number) - (b.sequence_num as number)
    );
    const map = new Map(this.jobLogs());
    map.set(jobId, updated);
    this.jobLogs.set(map);
  }

  private _mergeToolProgress(jobId: string, tp: ToolProgressData): void {
    const outer = new Map(this.toolProgressMap());
    const inner = new Map(outer.get(jobId) ?? []);
    if (tp.status === 'running') {
      inner.set(tp.call_group_id, tp);
    } else {
      inner.delete(tp.call_group_id);
    }
    outer.set(jobId, inner);
    this.toolProgressMap.set(outer);
  }

  private _clearToolProgress(jobId: string, callGroupId: string): void {
    const outer = new Map(this.toolProgressMap());
    const inner = new Map(outer.get(jobId) ?? []);
    inner.delete(callGroupId);
    outer.set(jobId, inner);
    this.toolProgressMap.set(outer);
  }

  /** Returns 0-100 percentage or null when total is unknown (indeterminate bar). */
  progressPct(tp: ToolProgressData): number | null {
    if (!tp.total_units) return null;
    return Math.min(100, Math.round((tp.processed_units / tp.total_units) * 100));
  }

  selectLog(id: string): void {
    this.selectedLogId.set(this.selectedLogId() === id ? null : id);
  }

  truncate(text: string | null, limit = 100): string {
    if (!text) return '—';
    return text.length > limit ? text.slice(0, limit) + '…' : text;
  }

  openDialog(label: string, raw: string | null): void {
    const text = raw ?? '';
    const { formatted, isJson } = formatForDialog(text);
    this.dialog.set({ label, text, formatted, isJson, copied: false });
  }

  /** Rewrite internal Azurite blob URLs to the API gateway blob-proxy so they
   *  are accessible from the browser outside the Docker network. */
  private rewriteBlobUrl(url: string): string {
    const AZURITE_PREFIX = 'http://azurite:10000/devstoreaccount1/';
    if (url.startsWith(AZURITE_PREFIX)) {
      return `${environment.apiUrl}/v1/blob-proxy/${url.slice(AZURITE_PREFIX.length)}`;
    }
    return url;
  }

  private escapeHtml(s: string): string {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /** Convert plain text to SafeHtml, wrapping any detected URLs in clickable
   *  anchor tags that open in a new tab. Azurite internal URLs are rewritten
   *  to the API gateway blob-proxy before use as href values. */
  private renderWithLinks(text: string): SafeHtml {
    const urlRegex = /(https?:\/\/[^\s"'<>]+)/g;
    let html = '';
    let lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = urlRegex.exec(text)) !== null) {
      html += this.escapeHtml(text.slice(lastIndex, match.index));
      const href = this.escapeHtml(this.rewriteBlobUrl(match[0]));
      const display = this.escapeHtml(match[0]);
      html += `<a href="${href}" target="_blank" rel="noopener noreferrer" class="dialog__link">${display}</a>`;
      lastIndex = match.index + match[0].length;
    }
    html += this.escapeHtml(text.slice(lastIndex));
    return this.sanitizer.bypassSecurityTrustHtml(html);
  }

  closeDialog(): void {
    this.dialog.set(null);
  }

  copyDialog(): void {
    const d = this.dialog();
    if (!d) return;
    navigator.clipboard.writeText(d.text).then(() => {
      this.dialog.set({ ...d, copied: true });
      setTimeout(() => {
        const current = this.dialog();
        if (current?.copied) this.dialog.set({ ...current, copied: false });
      }, 2000);
    });
  }

  private loadJobs(): void {
    this.loading.set(true);
    this.api.listJobs(this.historyFilter())
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({
        next: ({ jobs }) => {
          this.jobs.set(jobs);
          this.loadJobFiles(jobs);
          this.loadAllJobLogs(jobs);
          // Refresh logs for any currently-expanded job
          const expanded = this.expandedJob();
          if (expanded) {
            this.fetchJobLogs(expanded);
          }
        },
        error: (err) => console.error('[Dashboard] listJobs failed', err),
      });
  }

  private fetchJobLogs(jobId: string): void {
    this.api.getJobLogs(jobId).pipe(
      catchError(() => of({ logs: [] as JobLog[] }))
    ).subscribe(({ logs }) => {
      // Merge REST result with any live-streamed entries already in the buffer,
      // keeping highest-fidelity set sorted by sequence_num.
      const current = this.jobLogs().get(jobId) ?? [];
      const merged = [...logs];
      for (const live of current) {
        if (!merged.some(l => l.sequence_num === live.sequence_num)) {
          merged.push(live);
        }
      }
      merged.sort((a, b) => (a.sequence_num as number) - (b.sequence_num as number));
      const map = new Map(this.jobLogs());
      map.set(jobId, merged);
      this.jobLogs.set(map);
    });
  }

  /** Silently fetch logs for all jobs that haven't been loaded yet,
   *  so model call counts are visible in the job header on page load. */
  private loadAllJobLogs(jobs: Job[]): void {
    const current = this.jobLogs();
    for (const job of jobs) {
      if (!current.has(job.id)) {
        this.fetchJobLogs(job.id);
      }
    }
  }

  /** Handle <input type="file"> change event — read each file and parse. */
  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    for (const file of files) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const content = e.target?.result as string;
        if (content) this._parseLogFile(file.name, content);
      };
      reader.readAsText(file);
    }
    // Reset so the same file can be re-selected after clearing
    input.value = '';
  }

  /** Remove all file-loaded session groups and their log entries. */
  clearFileGroups(): void {
    const groups = this.fileSessionGroups();
    // Remove job logs for file jobs
    const map = new Map(this.jobLogs());
    for (const group of groups) {
      for (const job of group.jobs) {
        this._fileJobIds.delete(job.id);
        map.delete(job.id);
      }
    }
    this.jobLogs.set(map);
    this.fileSessionGroups.set([]);
    // If expanded job was from a file, collapse it
    const expanded = this.expandedJob();
    if (expanded && !this.jobs().some(j => j.id === expanded)) {
      this.expandedJob.set(null);
    }
  }

  /** Parse a CI log file into a SessionGroup + pre-populated logs map. */
  private _parseLogFile(filename: string, content: string): void {
    const lines = content.split('\n');

    // Derive test name from the header line "=== <path> ==="
    let testName = filename.replace(/\.log$/, '');
    const headerMatch = lines[0]?.match(/^===\s+(.+?)\s+===$/);
    if (headerMatch) testName = headerMatch[1];

    // Derive a stable synthetic session ID from the filename
    const sessionId = 'file-' + filename.replace(/[^a-zA-Z0-9]/g, '').slice(0, 24);

    const jobs: Job[] = [];
    const logsMap = new Map<string, JobLog[]>();

    let currentJobId: string | null = null;
    let currentLogs: JobLog[] = [];
    let seqNum = 0;
    let firstTimestamp: string | null = null;
    let lastTimestamp: string | null = null;
    let groupIndex = 0;

    type EntryBuf = {
      ts: string; log_type: string;
      tool: string | null; agent: string | null;
      msgLines: string[]; errLines: string[];
    };
    let currentEntry: EntryBuf | null = null;

    const flushEntry = () => {
      if (!currentEntry || !currentJobId) return;
      const message = currentEntry.msgLines.join('\n').trim() || null;
      const error_text = currentEntry.errLines.join('\n').trim() || null;
      const message_type = error_text ? 'Error' : 'Input';
      currentLogs.push({
        id: `file-${currentJobId}-${seqNum}`,
        job_id: currentJobId,
        session_id: sessionId,
        service_name: currentEntry.tool ? 'mcp-server-analysis' : 'agent-orchestrator',
        log_type: currentEntry.log_type,
        model_id: null,
        tool_name: currentEntry.tool,
        agent_role: currentEntry.agent,
        task_name: null,
        message,
        message_type,
        call_group_id: `file-grp-${currentJobId}-${groupIndex}`,
        sequence_num: seqNum++,
        error_text,
        created_at: currentEntry.ts,
      } as JobLog);
    };

    const saveJob = () => {
      if (!currentJobId) return;
      flushEntry();
      currentEntry = null;
      logsMap.set(currentJobId, [...currentLogs]);
      const hasError = currentLogs.some(l => l.message_type === 'Error');
      jobs.push({
        id: currentJobId,
        user_id: 'file',
        session_id: sessionId,
        video_id: '',
        prompt: testName,
        status: hasError ? 'failed' : 'completed',
        output_url: null,
        error: null,
        is_test: true,
        created_at: firstTimestamp ?? new Date().toISOString(),
        updated_at: lastTimestamp ?? new Date().toISOString(),
      } as Job);
    };

    for (const line of lines) {
      // Job section header: --- Job <uuid> ---
      const jobMatch = line.match(/^---\s+Job\s+([0-9a-f-]{36})\s+---$/);
      if (jobMatch) {
        saveJob();
        currentJobId = jobMatch[1];
        currentLogs = [];
        seqNum = 0;
        groupIndex = 0;
        firstTimestamp = null;
        lastTimestamp = null;
        currentEntry = null;
        continue;
      }

      // Log entry header: [timestamp] [log_type] (optional tool=xxx agent=xxx)
      const entryMatch = line.match(/^\[([^\]]+)\]\s+\[([^\]]+)\](.*)/);
      if (entryMatch && currentJobId) {
        flushEntry();
        groupIndex++;
        const ts = entryMatch[1];
        const log_type = entryMatch[2];
        const rest = entryMatch[3];
        if (!firstTimestamp) firstTimestamp = ts;
        lastTimestamp = ts;
        const toolMatch = rest.match(/tool=(\S+)/);
        const agentMatch = rest.match(/agent=(\S+)/);
        currentEntry = {
          ts,
          log_type,
          tool: toolMatch ? toolMatch[1] : null,
          agent: agentMatch ? agentMatch[1] : null,
          msgLines: [],
          errLines: [],
        };
        continue;
      }

      // Content line (2-space indent)
      if (currentEntry && line.startsWith('  ')) {
        const body = line.slice(2);
        if (body.startsWith('ERROR: ')) {
          currentEntry.errLines.push(body.slice(7));
        } else {
          currentEntry.msgLines.push(body);
        }
      }
    }

    // Flush final job
    saveJob();

    if (jobs.length === 0) return;

    // Register logs and job IDs
    for (const [jobId, logs] of logsMap) {
      this._fileJobIds.add(jobId);
    }
    const logsSignal = new Map(this.jobLogs());
    for (const [jobId, logs] of logsMap) {
      logsSignal.set(jobId, logs);
    }
    this.jobLogs.set(logsSignal);

    // Build session group label from test name (last :: segment)
    const labelParts = testName.split('::');
    const label = labelParts[labelParts.length - 1] ?? testName;

    const group: SessionGroup = {
      sessionId,
      label,
      date: jobs[0].created_at,
      jobs,
      isTest: true,
      isFromFile: true,
    };

    // Upsert group (replace if same session ID already loaded)
    this.fileSessionGroups.update(groups => {
      const idx = groups.findIndex(g => g.sessionId === sessionId);
      if (idx >= 0) {
        const updated = [...groups];
        updated[idx] = group;
        return updated;
      }
      return [...groups, group];
    });
  }

  private loadJobFiles(jobs: Job[]): void {
    if (jobs.length === 0) return;

    const calls = jobs.map(job => {
      const outputs$ = (job.status === 'completed' || job.status === 'failed')
        ? this.api.getJobOutputs(job.id).pipe(catchError(() => of({ outputs: [] as Output[] })))
        : of({ outputs: [] as Output[] });

      const inputs$ = job.session_id
        ? this.api.getSessionAssets(job.session_id).pipe(
            map(({ assets }) => ({
              assets: assets.filter(a => a.asset_type === 'uploaded_video' || a.asset_type === 'uploaded_file'),
            })),
            catchError(() => of({ assets: [] as SessionAsset[] })),
          )
        : of({ assets: [] as SessionAsset[] });

      return forkJoin({ outputs: outputs$, inputs: inputs$ }).pipe(
        map(({ outputs, inputs }) => ({ jobId: job.id, outputs: outputs.outputs, inputs: inputs.assets })),
      );
    });

    const byDate = (a: { created_at: string }, b: { created_at: string }) =>
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime();

    forkJoin(calls).pipe(catchError((err) => {
      console.error('[Dashboard] loadJobFiles failed', err);
      return of([]);
    })).subscribe(results => {
      const map = new Map<string, JobFiles>();
      for (const r of results) {
        map.set(r.jobId, {
          outputs: r.outputs.slice().sort(byDate),
          inputs: r.inputs.slice().sort(byDate),
        });
      }
      this.jobFiles.set(map);
    });
  }
}
