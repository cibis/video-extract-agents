import { Injectable } from '@angular/core';

const ACTIVE_KEY = 'lc_active_conversation';
const MAP_KEY = 'lc_job_conversation_map';
const PENDING_JOB_ID_KEY = 'lc_pending_job_id';

@Injectable({ providedIn: 'root' })
export class ConversationService {

  getActiveConversationId(): string | null {
    return localStorage.getItem(ACTIVE_KEY);
  }

  setActiveConversationId(id: string | null): void {
    if (id) {
      localStorage.setItem(ACTIVE_KEY, id);
    } else {
      localStorage.removeItem(ACTIVE_KEY);
    }
  }

  /**
   * Called when a new conversation is intentionally started (first visit or button click).
   * Clears all pending/active state so ngAfterViewInit creates a fresh draft job.
   */
  createNewConversationSlot(): void {
    localStorage.removeItem(ACTIVE_KEY);
    localStorage.removeItem(PENDING_JOB_ID_KEY);
    // Clear legacy keys that may exist from previous sessions
    localStorage.removeItem('lc_pending_conversation');
    localStorage.removeItem('lc_pending_conv_id');
  }

  /**
   * Clears the pending job slot and persists the real conversation ID along with
   * its job association. Called when JOB_SUBMITTED fires with a non-null conversationId.
   */
  activateConversation(jobId: string, conversationId: string): void {
    localStorage.removeItem(PENDING_JOB_ID_KEY);
    this.setActiveConversationId(conversationId);
    this.saveJobConversation(jobId, conversationId);
  }

  persistPendingJobId(jobId: string): void {
    localStorage.setItem(PENDING_JOB_ID_KEY, jobId);
  }

  getPendingJobId(): string | null {
    return localStorage.getItem(PENDING_JOB_ID_KEY);
  }

  saveJobConversation(jobId: string, conversationId: string): void {
    const map = this.readMap();
    map[jobId] = conversationId;
    localStorage.setItem(MAP_KEY, JSON.stringify(map));
  }

  getConversationForJob(jobId: string): string | null {
    return this.readMap()[jobId] ?? null;
  }

  private readMap(): Record<string, string> {
    try {
      return JSON.parse(localStorage.getItem(MAP_KEY) ?? '{}') as Record<string, string>;
    } catch {
      return {};
    }
  }
}
