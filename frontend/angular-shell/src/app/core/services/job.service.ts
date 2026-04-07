import { Injectable } from '@angular/core';
import { Observable, Subject } from 'rxjs';
import { environment } from '../../../environments/environment';
import { JobLog } from './api.service';

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
  readonly jobCompleted$ = new Subject<JobCompletedNotification>();

  notifyJobCompleted(event: JobCompletedNotification): void {
    this.jobCompleted$.next(event);
  }

  /**
   * Open an SSE stream for real-time job progress and log updates.
   * Emits JobProgressEvent (type='progress'|'status') and JobLogEvent (type='log').
   * Completes when the job reaches a terminal status.
   */
  streamJobProgress(jobId: string): Observable<JobStreamEvent> {
    return new Observable(observer => {
      const url = `${environment.apiUrl}/v1/jobs/${jobId}/stream`;
      const es = new EventSource(url);

      es.onmessage = (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data) as JobStreamEvent;
          observer.next(data);
          if (data.type === 'status' && (data.status === 'completed' || data.status === 'failed')) {
            es.close();
            observer.complete();
          }
        } catch {
          // ignore parse errors
        }
      };

      es.onerror = () => {
        es.close();
        observer.error(new Error('SSE connection lost'));
      };

      return () => es.close();
    });
  }
}
