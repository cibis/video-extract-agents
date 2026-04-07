import {
  Component,
  Input,
  Output,
  EventEmitter,
  OnInit,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  ElementRef,
  ViewChild,
  inject,
} from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { environment } from '../../../../environments/environment';
import { AuthService } from '../../../core/auth/auth.service';
import { ApiService } from '../../../core/services/api.service';

export interface JobSubmittedEvent {
  jobId: string;
  prompt: string;
}

export interface JobCompletedEvent {
  jobId: string;
  outputUrl: string;
}

@Component({
  selector: 'app-librechat-iframe',
  standalone: true,
  template: `
    <div class="iframe-wrapper">
      @if (iframeSrc) {
        <iframe
          #chatFrame
          [src]="iframeSrc"
          [title]="'Video Extract Chat'"
          frameborder="0"
          allow="clipboard-write"
          class="chat-iframe"
          (load)="onIframeLoad()">
        </iframe>
      }
    </div>
  `,
  styles: [`
    .iframe-wrapper {
      width: 100%;
      height: 100%;
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    .chat-iframe {
      width: 100%;
      height: 100%;
      border: none;
    }
  `],
})
export class LibrechatIframeComponent implements OnInit, OnChanges, OnDestroy {
  @Input() sessionId: string | null = null;
  @Input() videoIds: string[] = [];
  @Input() jobId: string | null = null;
  @Output() jobSubmitted = new EventEmitter<JobSubmittedEvent>();
  @Output() jobCompleted = new EventEmitter<JobCompletedEvent>();

  @ViewChild('chatFrame') chatFrameRef?: ElementRef<HTMLIFrameElement>;

  iframeSrc: SafeResourceUrl | null = null;

  private entraToken: string | null = null;
  private _iframeLoaded = false;

  private sanitizer = inject(DomSanitizer);
  private authService = inject(AuthService);
  private apiService = inject(ApiService);

  async ngOnInit(): Promise<void> {
    window.addEventListener('message', this.handleMessage);
    await this.initIframe();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if ((changes['sessionId'] || changes['videoIds']) && this._iframeLoaded && this.sessionId) {
      this.sendSessionContext(this.sessionId, this.videoIds);
    }

    if (changes['jobId'] && this._iframeLoaded) {
      this.sendJobContext(this.jobId);
    }
  }

  ngOnDestroy(): void {
    window.removeEventListener('message', this.handleMessage);
  }

  /** Navigates the iframe back to /c/new, clearing any active conversation. */
  async reset(): Promise<void> {
    this._iframeLoaded = false;
    this.iframeSrc = null;
    await this.initIframe();
  }

  private async initIframe(): Promise<void> {
    this.entraToken = this.authService.getToken();
    const targetUrl = `${environment.librechatUrl}/c/new`;
    try {
      await this.apiService.provisionLibrechatUser(this.entraToken);
      this.iframeSrc = this.sanitizer.bypassSecurityTrustResourceUrl(targetUrl);
    } catch (err) {
      console.error('[LibrechatIframe] Provisioning failed — loading without SSO:', err);
      this.iframeSrc = this.sanitizer.bypassSecurityTrustResourceUrl(targetUrl);
    }
  }

  onIframeLoad(): void {
    if (!this.chatFrameRef) return;
    const win = this.chatFrameRef.nativeElement.contentWindow;
    if (!win) return;

    this._iframeLoaded = true;

    if (this.entraToken) {
      win.postMessage(
        { type: 'AUTH_TOKEN', token: this.entraToken },
        environment.librechatUrl,
      );
    }

    if (this.sessionId) {
      this.sendSessionContext(this.sessionId, this.videoIds);
    }

    if (this.jobId) {
      this.sendJobContext(this.jobId);
    }
  }

  private sendJobContext(jobId: string | null): void {
    const win = this.chatFrameRef?.nativeElement.contentWindow;
    if (!win) return;
    win.postMessage(
      { type: 'JOB_CONTEXT', jobId },
      environment.librechatUrl,
    );
  }

  private sendSessionContext(sessionId: string, videoIds: string[]): void {
    const win = this.chatFrameRef?.nativeElement.contentWindow;
    if (!win) return;
    win.postMessage(
      { type: 'SESSION_CONTEXT', sessionId, videoIds, apiUrl: environment.apiUrl },
      environment.librechatUrl,
    );
  }

  private handleMessage = (event: MessageEvent): void => {
    if (typeof event.data !== 'object' || !event.data?.type) return;

    const { type, jobId, prompt, outputUrl } = event.data;

    if (type === 'JOB_SUBMITTED') {
      this.jobSubmitted.emit({ jobId, prompt });
    } else if (type === 'JOB_COMPLETED') {
      this.jobCompleted.emit({ jobId, outputUrl });
    }
  };
}
