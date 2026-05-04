import {
  Component,
  Input,
  OnChanges,
  SimpleChanges,
  Output,
  EventEmitter,
  signal,
  computed,
  inject,
  ViewChild,
  ElementRef,
  OnInit,
  OnDestroy,
} from '@angular/core';
import { ApiService, SessionAsset } from '../../../core/services/api.service';
import { UploadService } from '../../../core/services/upload.service';
import { Observable, Subscription } from 'rxjs';

export interface UploadCompleteEvent {
  sessionId: string;
  videoIds: string[];
  assetIds: string[];
}

interface FileStatus {
  name: string;
  isVideo: boolean;
  status: 'queued' | 'uploading' | 'done' | 'indexing' | 'indexed' | 'failed';
  error?: string;
  downloadUrl?: string;
  videoId?: string;
}

@Component({
  selector: 'app-video-upload',
  standalone: true,
  imports: [],
  template: `
    <div class="upload"
      [class.upload--dragging]="dragging()"
      (dragover)="onDragOver($event)"
      (dragleave)="dragging.set(false)"
      (drop)="onDrop($event)">

      <!-- Drop zone — always visible unless actively uploading -->
      @if (!uploading()) {
        <label class="upload__label">
          <input
            #fileInput
            type="file"
            accept="video/*,application/json,text/csv,text/plain,image/*"
            multiple
            class="upload__input"
            (change)="onFileChange($event)">
          <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/>
            <line x1="12" y1="3" x2="12" y2="15"/>
          </svg>
          @if (!hasPreviousUploads()) {
            <span>Drop files here or click to browse</span>
            <small>Videos (MP4, MOV, AVI, MKV) · JSON, CSV, TXT, images — up to 10GB per video</small>
          } @else {
            <span>Add more files to this session. Wait uploaded files to be indexed before submitting a task.</span>
            <small>{{ totalVideoCount() }} video(s) · {{ totalAssetCount() }} other file(s) uploaded so far</small>
          }
        </label>
      }

      <!-- File list (cumulative across all batches) -->
      @if (fileStatuses().length > 0) {
        <ul class="upload__file-list">
          @for (f of fileStatuses(); track f.name + f.status) {
            <li class="upload__file"
              [class.upload__file--done]="f.status === 'done' || f.status === 'indexed'"
              [class.upload__file--uploading]="f.status === 'uploading'"
              [class.upload__file--indexing]="f.status === 'indexing'"
              [class.upload__file--failed]="f.status === 'failed'">
              <span class="upload__file-icon">{{ f.isVideo ? '🎬' : '📄' }}</span>
              <span class="upload__file-name">{{ f.name }}</span>
              <span class="upload__file-status">
                @if (f.status === 'queued') { waiting }
                @if (f.status === 'uploading') { uploading… }
                @if (f.status === 'done') {
                  @if (f.downloadUrl) {
                    <a class="upload__file-download" [href]="f.downloadUrl" target="_blank" download (click)="$event.stopPropagation()">↓ download</a>
                  } @else { ✓ }
                }
                @if (f.status === 'indexing') {
                  <span class="upload__file-indexing-dot"></span>indexing…
                }
                @if (f.status === 'indexed') { ✓ indexed }
                @if (f.status === 'failed') { ✗ {{ f.error ?? 'failed' }} }
              </span>
            </li>
          }
        </ul>
      }

      @if (uploading()) {
        <p class="upload__uploading-msg">Uploading {{ currentBatchSize() }} file(s)…</p>
      }

      @if (error()) {
        <p class="upload__error">{{ error() }}</p>
      }
    </div>
  `,
  styles: [`
    .upload {
      border: 2px dashed #ccc;
      border-radius: 8px;
      padding: 1.5rem;
      text-align: center;
      transition: border-color 0.2s, background 0.2s;
    }
    .upload--dragging {
      border-color: #0078d4;
      background: #f0f8ff;
    }
    .upload__input { display: none; }
    .upload__label {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.5rem;
      cursor: pointer;
      color: #555;
      small { color: #888; font-size: 0.8rem; }
    }
    .upload__file-list {
      list-style: none;
      margin: 1rem 0 0;
      padding: 0;
      text-align: left;
      max-height: 200px;
      overflow-y: auto;
    }
    .upload__file {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.25rem 0.5rem;
      border-radius: 4px;
      font-size: 0.875rem;
      color: #888;
    }
    .upload__file--done { color: #2e7d32; }
    .upload__file--uploading { color: #0078d4; }
    .upload__file--indexing { color: #0078d4; }
    .upload__file--failed { color: #c62828; }
    @keyframes indexing-pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50%       { opacity: 0.35; transform: scale(0.55); }
    }
    .upload__file-indexing-dot {
      display: inline-block;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: currentColor;
      margin-right: 5px;
      vertical-align: middle;
      animation: indexing-pulse 1.1s ease-in-out infinite;
    }
    .upload__file-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .upload__file-status { font-size: 0.8rem; white-space: nowrap; }
    .upload__file-download { font-size: 0.75rem; color: #0078d4; text-decoration: none; }
    .upload__file-download:hover { text-decoration: underline; }
    .upload__uploading-msg { color: #0078d4; font-size: 0.875rem; margin-top: 0.75rem; }
    .upload__error { color: #c62828; font-size: 0.875rem; margin-top: 0.5rem; }
  `],
})
export class VideoUploadComponent implements OnChanges, OnInit, OnDestroy  {
  /** Pass the active session ID from the parent to reuse it for subsequent batches. */
  @Input() existingSessionId: string | null = null;

  /** Session assets from the parent — used to populate download URLs on completed files. */
  @Input() sessionAssets: SessionAsset[] = [];

  @Output() uploaded = new EventEmitter<UploadCompleteEvent>();

  @ViewChild('fileInput') fileInputRef?: ElementRef<HTMLInputElement>;

  @Input() newSessionEvent!: Observable<void>;
  private sub = new Subscription();

  private api = inject(ApiService);
  private uploadService = inject(UploadService);

  private _indexingInterval: ReturnType<typeof setInterval> | null = null;

  dragging = signal(false);
  uploading = signal(false);
  fileStatuses = signal<FileStatus[]>([]);
  error = signal<string | null>(null);

  // Cumulative across all batches within the same session
  private sessionId = signal<string | null>(null);
  private videoIds = signal<string[]>([]);
  private assetIds = signal<string[]>([]);

  // Index into fileStatuses where the current batch starts
  private batchStartIndex = signal(0);

  hasPreviousUploads = computed(() => this.fileStatuses().length > 0 && !this.uploading());
  totalVideoCount = computed(() => this.videoIds().length);
  totalAssetCount = computed(() => this.assetIds().length);
  currentBatchSize = computed(() => this.fileStatuses().length - this.batchStartIndex());

  allDone = computed(() => {
    const statuses = this.fileStatuses();
    const start = this.batchStartIndex();
    if (statuses.length === 0 || start >= statuses.length) return false;
    return statuses.slice(start).every(
      f => f.status === 'done' || f.status === 'failed' || f.status === 'indexing' || f.status === 'indexed'
    );
  });

  ngOnInit() {
    this.sub = this.newSessionEvent.subscribe(() => {
      if (this._indexingInterval) {
        clearInterval(this._indexingInterval);
        this._indexingInterval = null;
      }
      this.fileStatuses.set([]);
      this.sessionId.set(null);
      this.videoIds.set([]);
      this.assetIds.set([]);
    });
  }

  ngOnDestroy() {
    this.sub.unsubscribe();
    if (this._indexingInterval) {
      clearInterval(this._indexingInterval);
      this._indexingInterval = null;
    }
  }

  ngOnChanges(changes: SimpleChanges): void {
    // When the parent passes updated session assets, populate download URLs on completed files.
    if (changes['sessionAssets'] && this.sessionAssets.length > 0) {
      this._applyDownloadUrls();
    }

    // When the parent restores a session ID on refresh, repopulate the file list from the server.
    if (
      changes['existingSessionId'] &&
      this.existingSessionId &&
      this.fileStatuses().length === 0
    ) {
      this.api.getSessionAssets(this.existingSessionId).subscribe({
        next: ({ assets }) => {
          if (this.fileStatuses().length > 0) return; // user already started uploading
          // Deduplicate by source_id — videos route and preprocessing worker both create rows.
          const seen = new Set<string>();
          const unique = assets.filter(a => {
            if (a.asset_type !== 'uploaded_video' && a.asset_type !== 'uploaded_file') return false;
            const key = a.source_id ?? a.id;
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
          });
          const restored: FileStatus[] = unique.map(a => ({
            name: a.filename ?? (a.asset_type === 'uploaded_video' ? 'video' : 'file'),
            isVideo: a.asset_type === 'uploaded_video',
            status: (a.asset_type === 'uploaded_video' ? 'indexing' : 'done') as FileStatus['status'],
            downloadUrl: a.signed_url || undefined,
            videoId: a.asset_type === 'uploaded_video' ? (a.source_id ?? undefined) : undefined,
          }));
          if (restored.length === 0) return;
          this.fileStatuses.set(restored);
          this.sessionId.set(this.existingSessionId!);
          const videoIds = unique
            .filter(a => a.asset_type === 'uploaded_video' && a.source_id)
            .map(a => a.source_id!);
          const assetIds = unique
            .filter(a => a.asset_type === 'uploaded_file' && a.source_id)
            .map(a => a.source_id!);
          this.videoIds.set(videoIds);
          this.assetIds.set(assetIds);
          // Immediately fetch real status for each restored video (no polling delay)
          for (const a of unique.filter(u => u.asset_type === 'uploaded_video' && u.source_id)) {
            const videoId = a.source_id!;
            this.api.getVideoStatus(videoId).subscribe({
              next: ({ status }) => {
                const fs: FileStatus['status'] =
                  status === 'indexed' ? 'indexed' :
                  status === 'failed'  ? 'failed'  :
                  'indexing';
                this._setFileStatusByVideoId(videoId, fs);
              },
              error: () => {},
            });
          }
          // Always start the poller — it watches all non-indexed videos and
          // self-terminates once every video reaches 'indexed'
          if (videoIds.length > 0) this._startIndexingPoller();
        },
        error: () => { /* non-critical */ },
      });
    }
  }

  private _applyDownloadUrls(): void {
    const byFilename = new Map(
      this.sessionAssets
        .filter(a => a.filename && a.signed_url)
        .map(a => [a.filename!, a.signed_url]),
    );
    this.fileStatuses.update(statuses =>
      statuses.map(f =>
        f.status === 'done' && !f.downloadUrl && byFilename.has(f.name)
          ? { ...f, downloadUrl: byFilename.get(f.name) }
          : f,
      ),
    );
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.dragging.set(true);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragging.set(false);
    const files = event.dataTransfer?.files;
    if (files?.length) this.startUploads(Array.from(files));
  }

  onFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = input.files;
    if (files?.length) this.startUploads(Array.from(files));
    // Reset input value so the same file can be re-selected if needed
    input.value = '';
  }

  private startUploads(files: File[]): void {
    this.error.set(null);
    this.uploading.set(true);

    // Record where this batch starts in the cumulative file list
    this.batchStartIndex.set(this.fileStatuses().length);

    // Append new file status entries (don't reset existing ones)
    this.fileStatuses.update(existing => [
      ...existing,
      ...files.map(f => ({
        name: f.name,
        isVideo: f.type.startsWith('video/'),
        status: 'queued' as const,
      })),
    ]);

    const existingId = this.existingSessionId ?? this.sessionId();

    if (existingId) {
      // Reuse existing session — upload immediately
      this.sessionId.set(existingId);
      this._uploadSequentially(files, 0, existingId);
    } else {
      // First batch — create a new session
      this.api.createSession().subscribe({
        next: ({ sessionId }) => {
          this.sessionId.set(sessionId);
          this._uploadSequentially(files, 0, sessionId);
        },
        error: err => {
          this.uploading.set(false);
          this.error.set('Failed to create session: ' + (err.message ?? String(err)));
        },
      });
    }
  }

  private _uploadSequentially(files: File[], index: number, sessionId: string): void {
    if (index >= files.length) {
      this.uploading.set(false);
      this._emitIfComplete();
      return;
    }

    const file = files[index];
    this._setFileStatus(file.name, 'uploading');

    const onError = (err: { message?: string }): void => {
      this._setFileStatus(file.name, 'failed', err.message ?? 'Upload failed');
      this._uploadSequentially(files, index + 1, sessionId);
    };

    if (file.type.startsWith('video/')) {
      this.uploadService.uploadVideo(file, sessionId).subscribe({
        next: (videoId: string) => {
          this._setFileStatusWithVideoId(file.name, 'indexing', videoId);
          this.videoIds.update(ids => [...ids, videoId]);
          this._startIndexingPoller();
          this._uploadSequentially(files, index + 1, sessionId);
        },
        error: onError,
      });
    } else {
      this.uploadService.uploadAsset(file, sessionId).subscribe({
        next: ({ assetId }) => {
          this._setFileStatus(file.name, 'done');
          this.assetIds.update(ids => [...ids, assetId]);
          this._uploadSequentially(files, index + 1, sessionId);
        },
        error: onError,
      });
    }
  }

  private _setFileStatusWithVideoId(name: string, status: FileStatus['status'], videoId: string): void {
    const statuses = this.fileStatuses();
    const lastIdx = [...statuses].map(f => f.name).lastIndexOf(name);
    if (lastIdx === -1) return;
    this.fileStatuses.update(s =>
      s.map((f, i) => i === lastIdx ? { ...f, status, videoId } : f)
    );
  }

  private _setFileStatusByVideoId(videoId: string, status: FileStatus['status']): void {
    this.fileStatuses.update(s =>
      s.map(f => f.videoId === videoId ? { ...f, status } : f)
    );
  }

  private _startIndexingPoller(): void {
    if (this._indexingInterval) return;
    this._indexingInterval = setInterval(() => this._pollIndexingStatuses(), 3000);
  }

  private _pollIndexingStatuses(): void {
    const allVideos = this.fileStatuses().filter(f => f.videoId);
    if (allVideos.length === 0 || allVideos.every(f => f.status === 'indexed')) {
      clearInterval(this._indexingInterval!);
      this._indexingInterval = null;
      return;
    }
    for (const f of allVideos.filter(f => f.status !== 'indexed')) {
      this.api.getVideoStatus(f.videoId!).subscribe({
        next: ({ status }) => {
          const mapped: FileStatus['status'] =
            status === 'indexed' ? 'indexed' :
            status === 'failed'  ? 'failed'  :
            'indexing';
          this._setFileStatusByVideoId(f.videoId!, mapped);
        },
        error: () => {},
      });
    }
  }

  private _setFileStatus(name: string, status: FileStatus['status'], error?: string): void {
    // Update only the last occurrence with this name (current batch)
    const statuses = this.fileStatuses();
    const lastIdx = [...statuses].map(f => f.name).lastIndexOf(name);
    if (lastIdx === -1) return;
    this.fileStatuses.update(s =>
      s.map((f, i) => i === lastIdx ? { ...f, status, error } : f)
    );
  }

  private _emitIfComplete(): void {
    const sessionId = this.sessionId();
    if (!sessionId) return;
    this.uploaded.emit({
      sessionId,
      videoIds: this.videoIds(),
      assetIds: this.assetIds(),
    });
  }
}
