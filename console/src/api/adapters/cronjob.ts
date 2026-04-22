/**
 * CronJob adapter — calls native HubOS backend directly.
 *
 * Native routes (prefix `/cron` under `/api`):
 * - GET    /api/cron/jobs                     → CronJobSpec[]
 * - POST   /api/cron/jobs                     → CronJobSpec
 * - GET    /api/cron/jobs/{job_id}            → CronJobView
 * - PUT    /api/cron/jobs/{job_id}            → CronJobSpec
 * - DELETE /api/cron/jobs/{job_id}            → {deleted}
 * - POST   /api/cron/jobs/{job_id}/pause      → {paused}
 * - POST   /api/cron/jobs/{job_id}/resume     → {resumed}
 * - POST   /api/cron/jobs/{job_id}/run        → {started}
 * - GET    /api/cron/jobs/{job_id}/state      → CronJobState
 *
 * The backend spec mirrors `CronJobSpecOutput` (nested `schedule` object), so
 * this adapter is a thin pass-through.
 */

import { request } from "../request";
import type {
  CronJobSpecInput,
  CronJobSpecOutput,
  CronJobView,
} from "../types";

const JOBS = "/cron/jobs";

export const cronJobAdapter = {
  listCronJobs: () => request<CronJobSpecOutput[]>(JOBS),

  createCronJob: (spec: CronJobSpecInput) =>
    request<CronJobSpecOutput>(JOBS, {
      method: "POST",
      body: JSON.stringify(spec),
    }),

  getCronJob: (jobId: string) =>
    request<CronJobView>(`${JOBS}/${encodeURIComponent(jobId)}`),

  replaceCronJob: (jobId: string, spec: CronJobSpecInput) =>
    request<CronJobSpecOutput>(`${JOBS}/${encodeURIComponent(jobId)}`, {
      method: "PUT",
      body: JSON.stringify(spec),
    }),

  deleteCronJob: (jobId: string) =>
    request<{ deleted: boolean }>(
      `${JOBS}/${encodeURIComponent(jobId)}`,
      { method: "DELETE" },
    ),

  pauseCronJob: (jobId: string) =>
    request<{ paused: boolean }>(
      `${JOBS}/${encodeURIComponent(jobId)}/pause`,
      { method: "POST" },
    ),

  resumeCronJob: (jobId: string) =>
    request<{ resumed: boolean }>(
      `${JOBS}/${encodeURIComponent(jobId)}/resume`,
      { method: "POST" },
    ),

  runCronJob: (jobId: string) =>
    request<{ started: boolean }>(
      `${JOBS}/${encodeURIComponent(jobId)}/run`,
      { method: "POST" },
    ),

  triggerCronJob: (jobId: string) =>
    request<{ started: boolean }>(
      `${JOBS}/${encodeURIComponent(jobId)}/run`,
      { method: "POST" },
    ),

  getCronJobState: (jobId: string) =>
    request<unknown>(`${JOBS}/${encodeURIComponent(jobId)}/state`),
};
