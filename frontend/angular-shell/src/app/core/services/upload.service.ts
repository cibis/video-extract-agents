import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpEvent, HttpEventType, HttpHeaders, HttpRequest } from '@angular/common/http';
import { Observable } from 'rxjs';
import { ApiService } from './api.service';

export interface UploadProgress {
  percent: number;
  done: boolean;
}

@Injectable({ providedIn: 'root' })
export class UploadService {
  private http = inject(HttpClient);
  private api = inject(ApiService);

  /**
   * @deprecated Use uploadVideo(file, sessionId) for session-aware uploads.
   */
  upload(file: File): { videoId$: Observable<string> } {
    const videoId$ = new Observable<string>(observer => {
      this.api.requestSasUrl().subscribe({
        next: ({ videoId, uploadUrl }) => {
          this._putBlob(uploadUrl, file, file.type || 'video/mp4').subscribe({
            complete: () => { observer.next(videoId); observer.complete(); },
            error: err => observer.error(err),
          });
        },
        error: err => observer.error(err),
      });
    });
    return { videoId$ };
  }

  /**
   * Upload a video file under the given session.
   * Emits the videoId when the upload completes.
   */
  uploadVideo(file: File, sessionId: string): Observable<string> {
    return new Observable<string>(observer => {
      this.api.requestVideoSasUrl(sessionId, file.name).subscribe({
        next: ({ videoId, uploadUrl }) => {
          this._putBlob(uploadUrl, file, file.type || 'video/mp4').subscribe({
            complete: () => { observer.next(videoId); observer.complete(); },
            error: err => observer.error(err),
          });
        },
        error: err => observer.error(err),
      });
    });
  }

  /**
   * Upload a non-video asset under the given session.
   * Emits { assetId, filename, contentType } when the upload completes.
   */
  uploadAsset(
    file: File,
    sessionId: string,
  ): Observable<{ assetId: string; filename: string; contentType: string }> {
    const contentType = file.type || 'application/octet-stream';
    return new Observable(observer => {
      this.api.requestAssetSasUrl({ sessionId, filename: file.name, contentType }).subscribe({
        next: ({ assetId, uploadUrl }) => {
          this._putBlob(uploadUrl, file, contentType).subscribe({
            complete: () => {
              observer.next({ assetId, filename: file.name, contentType });
              observer.complete();
            },
            error: err => observer.error(err),
          });
        },
        error: err => observer.error(err),
      });
    });
  }

  private _putBlob(url: string, file: File, contentType: string): Observable<HttpEvent<unknown>> {
    const req = new HttpRequest('PUT', url, file, {
      headers: new HttpHeaders({
        'x-ms-blob-type': 'BlockBlob',
        'Content-Type': contentType,
      }),
      reportProgress: true,
    });
    return this.http.request(req);
  }
}
