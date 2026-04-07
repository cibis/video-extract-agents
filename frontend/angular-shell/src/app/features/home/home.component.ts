import { Component, AfterViewInit, OnDestroy, OnInit, inject, signal, computed, ElementRef, ViewChild } from '@angular/core';
import { Subject, firstValueFrom } from 'rxjs';
import { DatePipe } from '@angular/common';
import { VideoUploadComponent, UploadCompleteEvent } from '../shared/video-upload/video-upload.component';
import { LibrechatIframeComponent } from '../shared/librechat-iframe/librechat-iframe.component';
import { ConversationService } from '../../core/services/conversation.service';
import { ApiService, Job, Output, SessionAsset } from '../../core/services/api.service';

const SESSION_STORAGE_KEY = 'lc_session_id';

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [VideoUploadComponent, LibrechatIframeComponent, DatePipe],
  template: `
    <div class="home">
      <div class="home__left">
        <section class="home__upload">
          <h2>Upload Files</h2>
          <app-video-upload
            [existingSessionId]="currentSessionId()"
            [sessionAssets]="sessionAssets()"
            (uploaded)="onUploadComplete($event)"
            [newSessionEvent]="onNewSession$"
          />
          @if (currentSessionId()) {
            <p class="home__session-info">
              Session: <code>{{ currentSessionId() }}</code><br>
              {{ currentVideoIds().length }} video(s) · {{ currentAssetIds().length }} file(s)
            </p>
          }
          <button class="home__new-btn" [disabled]="creatingConversation()" (click)="onNewSession()">
            {{ creatingConversation() ? 'Creating…' : '+ New Session' }}
          </button>
        </section>
        @if (progressVisible()) {
          <div class="job-progress">
            <div class="job-progress__header">
              @if (jobDone()) {
                <span>✓ Done</span>
              } @else {
                <span><span class="job-progress__dot"></span> Agent running…</span>
              }
            </div>
            <ul class="job-progress__list" #progressList>
              @for (step of progressSteps(); track step.stepName; let last = $last) {
                <li class="job-progress__step" [class.job-progress__step--active]="last">
                  {{ last ? '▶' : '✓' }} {{ step.stepName }}
                </li>
              }
            </ul>
          </div>
        }
      </div>
      <section class="home__chat">
        @if (currentSessionJobs().length != 0) {
          <div class="home__history" #historyPanel>
            @if (historyLoading()) {
              <p class="home__history-empty">Loading history…</p>
            } @else if (currentSessionJobs().length === 0) {
              <p class="home__history-empty">No jobs for this session.</p>
            } @else {
              <ul class="home__history-list">
                @for (job of currentSessionJobs(); track job.id) {
                  <li class="home__history-item" [class]="'home__history-item--' + job.status">
                    <span class="home__history-prompt" [title]="job.prompt">{{ job.prompt }}</span>
                    <span class="home__history-meta">
                      <span class="home__history-status">{{ job.status }}</span>
                      <span class="home__history-date">{{ job.created_at | date:'shortTime' }}</span>
                      @for (out of (outputsMap().get(job.id) ?? []); track out.id) {
                        <a class="home__history-download" [href]="out.signed_url" target="_blank" download>⬇ {{ out.filename ?? 'output' }}</a>
                      }
                    </span>
                  </li>
                }
              </ul>
            }
          </div>
        }
        <div [class.disabled-div]="currentVideoIds().length === 0">
          <app-librechat-iframe
            #librechatIframe
            [sessionId]="currentSessionId()"
            [videoIds]="currentVideoIds()"
            [jobId]="currentJobId()"
          />
        </div>
      </section>
    </div>
  `,
  styles: [`
    .disabled-div {
      pointer-events: none;
      opacity: 0.5;
      cursor: not-allowed;
    }
    .home {
      display: grid;
      grid-template-columns: 1fr 2fr;
      gap: 2rem;
      align-items: start;
    }
    .home__left { display: flex; flex-direction: column; gap: 0.75rem; }
    .home__upload { padding: 1.5rem; background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .home__chat { display: flex; flex-direction: column; gap: 0.75rem; }
    .home__session-info { font-size: 0.85rem; color: #666; margin-top: 0.5rem; line-height: 1.6; }
    .home__new-btn {
      margin-top: 1rem;
      width: 100%;
      padding: 0.5rem 1rem;
      background: white;
      border: 1px solid #0078d4;
      color: #0078d4;
      border-radius: 4px;
      font-size: 0.875rem;
      cursor: pointer;
    }
    .home__new-btn:hover { background: #f0f6ff; }
    .home__new-btn:disabled { opacity: 0.6; cursor: not-allowed; }

    .home__history {
      background: white;
      border-radius: 8px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
      padding: 0.75rem 1rem;
      max-height: 160px;
      overflow-y: scroll;
    }
    .home__history-empty { font-size: 0.85rem; color: #999; margin: 0; }
    .home__history-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.3rem; }
    .home__history-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.5rem;
      padding: 0.35rem 0.5rem;
      border-radius: 4px;
      background: #f8f9fa;
      font-size: 0.8rem;
    }
    .home__history-item--completed { border-left: 3px solid #107c10; }
    .home__history-item--failed { border-left: 3px solid #d13438; }
    .home__history-item--processing { border-left: 3px solid #0078d4; }
    .home__history-item--queued { border-left: 3px solid #797775; }
    .home__history-prompt {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      color: #323130;
    }
    .home__history-meta { display: flex; flex-direction: column; align-items: flex-end; flex-shrink: 0; gap: 0.1rem; }
    .home__history-status { font-size: 0.7rem; font-weight: 600; text-transform: uppercase; color: #605e5c; }
    .home__history-date { font-size: 0.7rem; color: #a19f9d; }
    .home__history-download { font-size: 0.7rem; color: #0078d4; text-decoration: none; }
    .home__history-download:hover { text-decoration: underline; }

    app-librechat-iframe { display: block; height: 64vh; min-height: 300px; }

    /* Job progress panel */
    .job-progress {
      background: white;
      border-radius: 8px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.15);
      border-left: 3px solid #0078d4;
      padding: 0.75rem 1rem;
      font-size: 0.85rem;
    }
    .job-progress__header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.5rem;
      color: #323130;
      font-weight: 500;
    }
    .job-progress__dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #0078d4;
      margin-right: 6px;
      animation: pulse 1.5s ease-in-out infinite;
    }
    .job-progress__list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      max-height: 120px;
      overflow-y: auto;
    }
    .job-progress__step { color: #a19f9d; font-size: 0.8rem; }
    .job-progress__step--active { color: #323130; font-weight: 500; }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }

    @media (max-width: 768px) {
      .home { grid-template-columns: 1fr; }
    }
  `],
})
export class HomeComponent implements OnInit, AfterViewInit, OnDestroy {
  private conversationService = inject(ConversationService);
  private api = inject(ApiService);

  currentSessionId = signal<string | null>(null);
  currentVideoIds = signal<string[]>([]);
  currentAssetIds = signal<string[]>([]);
  currentJobId = signal<string | null>(null);
  creatingConversation = signal<boolean>(false);
  jobs = signal<Job[]>([]);
  outputsMap = signal<Map<string, Output[]>>(new Map());
  historyLoading = signal<boolean>(false);
  sessionAssets = signal<SessionAsset[]>([]);
  progressSteps = signal<{ stepName: string; stepStatus: string }[]>([]);
  progressVisible = signal(false);
  jobDone = signal(false);

  currentSessionJobs = computed(() => {
    const current = this.currentSessionId();
    return this.jobs().filter(j => j.prompt && j.session_id === current);
  });

  @ViewChild('historyPanel') historyPanel?: ElementRef<HTMLDivElement>;
  @ViewChild('progressList') progressList?: ElementRef<HTMLUListElement>;
  @ViewChild('librechatIframe') librechatIframe?: LibrechatIframeComponent;

  private _jobInterval?: ReturnType<typeof setInterval>;
  private _historyInterval?: ReturnType<typeof setInterval>;
  private _activeJobId: string | null = null;

  ngOnInit(): void {
    const storedSession = localStorage.getItem(SESSION_STORAGE_KEY);
    if (storedSession) {
      this.currentSessionId.set(storedSession);
      this.api.getSessionAssets(storedSession).subscribe({
        next: ({ assets }) => {
          const unique = (type: string) => [...new Map(
            assets.filter(a => a.asset_type === type && a.source_id).map(a => [a.source_id, a]),
          ).values()].map(a => a.source_id!);
          this.currentVideoIds.set(unique('uploaded_video'));
          this.currentAssetIds.set(unique('uploaded_file'));
          this.sessionAssets.set(assets.filter(a => a.signed_url));
        },
        error: () => {},
      });
    }

    this.loadHistory();
    this._historyInterval = setInterval(() => this.loadHistory(true), 5000);
  }

  private loadHistory(silent = false): void {
    if (!silent) this.historyLoading.set(true);
    this.api.listJobs().subscribe({
      next: ({ jobs }) => {
        const sorted = [...jobs].sort(
          (a, b) => new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime(),
        );
        const shouldUpdate = !silent || (() => {
          const current = this.jobs();
          return sorted.length !== current.length ||
            sorted.some((j, i) => j.id !== current[i]?.id || j.status !== current[i]?.status || j.updated_at !== current[i]?.updated_at);
        })();
        if (shouldUpdate) {
          this.jobs.set(sorted);
          this.fetchMissingOutputs(sorted);
          setTimeout(() => {
            if (this.historyPanel) {
              this.historyPanel.nativeElement.scrollTop = this.historyPanel.nativeElement.scrollHeight;
            }
          }, 50);
        }

        // Auto-detect new active jobs — works for second prompt and page refresh.
        const activeJob = sorted.find(j => j.status === 'queued' || j.status === 'processing');
        if (activeJob && activeJob.id !== this._activeJobId) {
          this.progressSteps.set([]);
          this.progressVisible.set(false);
          this.jobDone.set(false);
          this.currentJobId.set(activeJob.id);
          this.startJobPolling(activeJob.id);
        }
        if (!silent) this.historyLoading.set(false);
      },
      error: () => { if (!silent) this.historyLoading.set(false); },
    });
  }

  private fetchMissingOutputs(jobs: Job[]): void {
    const map = this.outputsMap();
    jobs
      .filter(j => j.status === 'completed' && !map.has(j.id))
      .forEach(j => {
        this.api.getJobOutputs(j.id).subscribe({
          next: ({ outputs }) => {
            this.outputsMap.update(m => {
              const next = new Map(m);
              next.set(j.id, outputs.filter(o => o.signed_url?.startsWith('http')));
              return next;
            });
          },
          error: () => {},
        });
      });
  }

  ngAfterViewInit(): void {
    // Always create a fresh draft on load so the job ID is guaranteed to exist
    // in the DB and matches what the chat route will find.
    void this.createNewConversation();
  }

  onNewSession(): void {
    this.currentSessionId.set(null);
    this.currentVideoIds.set([]);
    this.currentAssetIds.set([]);
    this.sessionAssets.set([]);
    this.jobs.set([]);
    this.outputsMap.set(new Map());
    this.currentJobId.set(null);
    this.stopJobPolling();
    this.progressSteps.set([]);
    this.progressVisible.set(false);
    this.jobDone.set(false);

    localStorage.removeItem(SESSION_STORAGE_KEY);
    this.conversationService.createNewConversationSlot();
    localStorage.removeItem('lc_job_conversation_map');

    void this.librechatIframe?.reset();
    void this.createNewConversation();
  }

  onUploadComplete(event: UploadCompleteEvent): void {
    this.currentSessionId.set(event.sessionId);
    this.currentVideoIds.set(event.videoIds);
    this.currentAssetIds.set(event.assetIds);
    localStorage.setItem(SESSION_STORAGE_KEY, event.sessionId);
    this.api.getSessionAssets(event.sessionId).subscribe({
      next: ({ assets }) => this.sessionAssets.set(assets.filter(a => a.signed_url)),
      error: () => {},
    });
  }

  ngOnDestroy(): void {
    this.stopJobPolling();
    clearInterval(this._historyInterval);
  }

  private onNewSessionSubject = new Subject<void>();
  onNewSession$ = this.onNewSessionSubject.asObservable();

  private startJobPolling(jobId: string): void {
    this.stopJobPolling();
    this._activeJobId = jobId;
    this.pollJob(jobId);
    this._jobInterval = setInterval(() => this.pollJob(jobId), 15000);
  }

  private stopJobPolling(): void {
    if (this._jobInterval !== undefined) {
      clearInterval(this._jobInterval);
      this._jobInterval = undefined;
    }
    this._activeJobId = null;
  }

  private pollJob(jobId: string): void {
    if (this._activeJobId !== jobId) return;

    this.api.getJobSteps(jobId).subscribe({
      next: ({ steps }) => {
        if (steps.length > 0) {
          this.progressSteps.set(steps.map(s => ({ stepName: s.step_name, stepStatus: s.status })));
          this.progressVisible.set(true);
          setTimeout(() => {
            if (this.progressList) {
              this.progressList.nativeElement.scrollTop = this.progressList.nativeElement.scrollHeight;
            }
          }, 50);
        }
      },
      error: () => {},
    });

    this.api.getJob(jobId).subscribe({
      next: (job) => {
        this.jobs.update(list => list.map(j => j.id === job.id ? job : j));
        if (job.status === 'completed' || job.status === 'failed') {
          this.stopJobPolling();
          this.jobDone.set(true);
          this.loadHistory(true);
        }
      },
      error: (err) => {
        // Stale job ID (e.g. DB reset while localStorage still holds old ID).
        // Create a fresh draft so the next chat submission has a valid job to use.
        if (err.status === 404 && this._activeJobId === jobId) {
          void this.createNewConversation();
        }
      },
    });
  }

  private async createNewConversation(): Promise<void> {
    this.creatingConversation.set(true);
    this.currentJobId.set(null);
    this.progressSteps.set([]);
    this.progressVisible.set(false);
    this.jobDone.set(false);
    this.conversationService.createNewConversationSlot();

    try {
      this.onNewSessionSubject.next();

      const { job } = await firstValueFrom(
        this.api.createDraftJob(this.currentSessionId()),
      );
      this.conversationService.persistPendingJobId(job.id);
      this.currentJobId.set(job.id);
      this.startJobPolling(job.id);
    } catch (err) {
      console.error('[Home] createNewConversation failed:', err);
    } finally {
      this.creatingConversation.set(false);
    }
  }
}
