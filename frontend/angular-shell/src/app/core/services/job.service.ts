import { Injectable, inject } from '@angular/core';
import { Observable, Subject } from 'rxjs';
import { environment } from '../../../environments/environment';
import { JobLog } from './api.service';
import { AuthService } from '../auth/auth.service';

export interface JobCompletedNotification {
  jobId: string;
  outputUrl?: string;
}

export interface JobProgressEvent {
  type: 'progress' | 'status';
  jobId: string;
  status?: string;
  outputUrl?: string;
  stepName?: string;
  stepStatus?: string;
}

export interface JobLogEvent {
  type: 'log';
  jobId: string;
  log: JobLog;
}

export interface ToolProgressData {
  call_group_id: string;
  job_id: string;
  tool_name: string;
  total_units: number | null;
  processed_units: number;
  unit_label: string;
  status: 'running' | 'completed' | 'failed';
  started_at: string;
  updated_at: string;
}

export interface JobToolProgressEvent {
  type: 'tool_progress';
  jobId: string;
  toolProgress: ToolProgressData;
}

export type JobStreamEvent = JobProgressEvent | JobLogEvent | JobToolProgressEvent;

@Injectable({ providedIn: 'root' })
export class JobService {
  private auth = inject(AuthService);
  readonly jobCompleted$ = new Subject<JobCompletedNotification>();

  notifyJobCompleted(event: JobCompletedNotification): void {
    this.jobCompleted$.next(event);
  }

  /**
   * Open an SSE stream for real-time job progress using fetch (not EventSource)
   * so the Authorization header can be included.  EventSource does not support
   * custom headers.
   */
  streamJobProgress(jobId: string): Observable<JobStreamEvent> {
    return new Observable(observer => {
      const url = `${environment.apiUrl}/v1/jobs/${jobId}/stream`;
      const token = this.auth.getToken();
      const controller = new AbortController();
      let done = false;

      (async () => {
        try {
          const response = await fetch(url, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            signal: controller.signal,
          });

          if (!response.ok || !response.body) {
            observer.error(new Error(`SSE stream failed: ${response.status}`));
            return;
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';

          while (!done) {
            const { done: streamDone, value } = await reader.read();
            if (streamDone) { observer.complete(); break; }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() ?? '';

            for (const line of lines) {
              if (!line.startsWith('data: ')) continue;
              try {
                const data = JSON.parse(line.slice(6)) as JobStreamEvent;
                observer.next(data);
                if (data.type === 'status' && (data.status === 'completed' || data.status === 'failed')) {
                  controller.abort();
                  observer.complete();
                  return;
                }
              } catch { /* ignore malformed event */ }
            }
          }
        } catch (err) {
          if (!done) observer.error(err);
        }
      })();

      return () => { done = true; controller.abort(); };
    });
  }
}
