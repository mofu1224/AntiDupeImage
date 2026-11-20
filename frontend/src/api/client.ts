import type {
  CancelResponse,
  DeleteResponse,
  FolderDialogRequest,
  FolderDialogResponse,
  GroupFilter,
  GroupSort,
  GroupSummariesResponse,
  GroupsResponse,
  MoveDuplicatesRequest,
  MoveDuplicatesResponse,
  ResetAppDataRequest,
  ResetAppDataResponse,
  ScanFailuresResponse,
  ScanListResponse,
  ScanRequest,
  ScanResponse,
  ScanState,
  ScanStatus,
  SystemResponse
} from "../types/api";

export const API_BASE = new URL(
  import.meta.env.VITE_API_BASE ?? (import.meta.env.PROD ? "/api" : "http://127.0.0.1:8000/api"),
  window.location.href
).toString().replace(/\/$/, "");
const BOOTSTRAP_URL = new URL("/_antidupeimage/bootstrap", API_BASE).toString();

let apiToken = loadInitialToken();

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getApiToken(): string {
  return apiToken;
}

export function setApiToken(value: string): void {
  apiToken = value.trim();
}

export async function bootstrapApiToken(): Promise<boolean> {
  const bootstrapToken = consumeFragmentValue("bootstrap");
  if (!bootstrapToken || apiToken) return false;

  const response = await fetch(BOOTSTRAP_URL, {
    method: "POST",
    headers: { "X-AntiDupeImage-Bootstrap": bootstrapToken }
  });
  if (!response.ok) {
    throw new ApiError(response.status, await responseMessage(response));
  }
  const payload = (await response.json()) as { api_token?: unknown };
  if (typeof payload.api_token !== "string" || !payload.api_token.trim()) {
    throw new ApiError(500, "Launcher bootstrap returned an invalid API token");
  }
  setApiToken(payload.api_token);
  return true;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(apiToken ? { "X-AntiDupeImage-Token": apiToken } : {}),
      ...(init?.headers ?? {})
    }
  });
  if (!response.ok) {
    throw new ApiError(response.status, await responseMessage(response));
  }
  return (await response.json()) as T;
}

export function getSystem(): Promise<SystemResponse> {
  return requestJson<SystemResponse>("/system");
}

export function resetAppData(): Promise<ResetAppDataResponse> {
  const payload: ResetAppDataRequest = { confirm: true };
  return requestJson<ResetAppDataResponse>("/system/reset", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function chooseFolder(initialPath?: string): Promise<FolderDialogResponse> {
  const payload: FolderDialogRequest = { initial_path: initialPath?.trim() || null };
  return requestJson<FolderDialogResponse>("/dialogs/folder", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function createScan(payload: ScanRequest): Promise<ScanResponse> {
  return requestJson<ScanResponse>("/scans", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function listScans(
  limit = 50,
  offset = 0,
  status?: ScanStatus
): Promise<ScanListResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) params.set("status", status);
  return requestJson<ScanListResponse>(`/scans?${params}`);
}

export function getScan(scanId: number): Promise<ScanState> {
  return requestJson<ScanState>(`/scans/${scanId}`);
}

export function cancelScan(scanId: number): Promise<CancelResponse> {
  return requestJson<CancelResponse>(`/scans/${scanId}/cancel`, { method: "POST" });
}

export function deleteScan(scanId: number): Promise<DeleteResponse> {
  return requestJson<DeleteResponse>(`/scans/${scanId}`, { method: "DELETE" });
}

export function getGroups(scanId: number, limit = 100, offset = 0): Promise<GroupsResponse> {
  return requestJson<GroupsResponse>(`/scans/${scanId}/groups?limit=${limit}&offset=${offset}`);
}

export function getGroupSummaries(
  scanId: number,
  limit = 100,
  offset = 0,
  filter: GroupFilter = "all",
  sort: GroupSort = "recommended"
): Promise<GroupSummariesResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    filter,
    sort
  });
  return requestJson<GroupSummariesResponse>(`/scans/${scanId}/group-summaries?${params}`);
}

export function getGroup(scanId: number, groupId: number): Promise<GroupsResponse["groups"][number]> {
  return requestJson<GroupsResponse["groups"][number]>(`/scans/${scanId}/groups/${groupId}`);
}

export function getFailures(scanId: number, limit = 100, offset = 0): Promise<ScanFailuresResponse> {
  return requestJson<ScanFailuresResponse>(`/scans/${scanId}/failures?limit=${limit}&offset=${offset}`);
}

export async function fetchThumbnail(imageId: number, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${API_BASE}/images/${imageId}/thumbnail`, {
    signal,
    headers: {
      ...(apiToken ? { "X-AntiDupeImage-Token": apiToken } : {})
    }
  });
  if (!response.ok) {
    throw new ApiError(response.status, await responseMessage(response));
  }
  return response.blob();
}

export function moveDuplicateFiles(
  scanId: number,
  groupId: number,
  imageIds: number[]
): Promise<MoveDuplicatesResponse> {
  const payload: MoveDuplicatesRequest = {
    scan_id: scanId,
    group_id: groupId,
    image_ids: imageIds,
    confirm: true
  };
  return requestJson<MoveDuplicatesResponse>("/files/move-duplicates", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function scanEventSource(scanId: number): EventSource {
  return new EventSource(withToken(`${API_BASE}/scans/${scanId}/events`));
}

function withToken(url: string): string {
  if (!apiToken) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(apiToken)}`;
}

async function responseMessage(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) return response.statusText;
  try {
    const payload = JSON.parse(text) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) {
      return payload.detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item) return String(item.msg);
          return JSON.stringify(item);
        })
        .join("; ");
    }
    return text;
  } catch {
    return text;
  }
}

export function loadInitialToken(): string {
  return consumeFragmentValue("token");
}

function consumeFragmentValue(name: string): string {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const value = params.get(name)?.trim() ?? "";
  if (!value) return "";
  params.delete(name);
  const remainingHash = params.toString();
  const cleanUrl = `${window.location.pathname}${window.location.search}${
    remainingHash ? `#${remainingHash}` : ""
  }`;
  window.history.replaceState(window.history.state, "", cleanUrl);
  return value;
}
