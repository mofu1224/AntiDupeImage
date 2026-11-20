import { useCallback, useEffect, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
  ReactNode
} from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  FileImage,
  FolderInput,
  FolderLock,
  Images,
  Lock,
  RefreshCw,
  ScanSearch
} from "lucide-react";
import {
  ApiError,
  cancelScan,
  createScan,
  deleteScan,
  getApiToken,
  getFailures,
  getGroup,
  getGroupSummaries,
  getScan,
  getSystem,
  listScans,
  moveDuplicateFiles,
  resetAppData,
  scanEventSource,
  setApiToken
} from "./api/client";
import { FailureList } from "./components/FailureList";
import { GroupList } from "./components/GroupList";
import type { GroupFilter } from "./components/GroupList";
import type { GroupSort } from "./components/GroupList";
import { clearThumbnailCache, ImageCompare } from "./components/ImageCompare";
import { ProgressPanel } from "./components/ProgressPanel";
import { ScanForm } from "./components/ScanForm";
import type { ScanFormTemplate } from "./components/ScanForm";
import { ScanHistory } from "./components/ScanHistory";
import type { ScanHistoryFilter } from "./components/ScanHistory";
import { SystemStatus } from "./components/SystemStatus";
import { useActionProgress } from "./hooks/useActionProgress";
import type { ActionProgress } from "./hooks/useActionProgress";
import {
  COMPARE_PANEL_WIDTH,
  REVIEW_SURFACE_HEIGHT,
  useWorkspaceLayout
} from "./hooks/useWorkspaceLayout";
import type {
  DuplicateGroup,
  GroupSummary,
  MoveDuplicatesResponse,
  ResetAppDataResponse,
  ScanFailure,
  ScanRequest,
  ScanState,
  ScanStatus,
  SystemResponse
} from "./types/api";

const GROUP_PAGE_SIZE = 100;
const FAILURE_PAGE_SIZE = 100;
const SCAN_PAGE_SIZE = 50;
type WorkspaceTab = "overview" | "scan" | "duplicates" | "exact" | "warnings";
const WORKSPACE_TAB_ORDER: WorkspaceTab[] = ["overview", "scan", "duplicates", "exact", "warnings"];
type GlobalErrorOwner =
  | "system"
  | "scans"
  | "failures"
  | "group-detail"
  | "group-summaries"
  | "progress"
  | "scan-start"
  | "scan-cancel"
  | "scan-delete"
  | "reset"
  | "move"
  | "scan-refresh";
type GlobalError = { owner: GlobalErrorOwner; message: string };

export default function App() {
  const [apiTokenValue, setApiTokenValue] = useState(getApiToken());
  const [system, setSystem] = useState<SystemResponse | null>(null);
  const [systemLoading, setSystemLoading] = useState(false);
  const [scan, setScan] = useState<ScanState | null>(null);
  const [scans, setScans] = useState<ScanState[]>([]);
  const [scanTotal, setScanTotal] = useState(0);
  const [scanOffset, setScanOffset] = useState(0);
  const [historyFilter, setHistoryFilter] = useState<ScanHistoryFilter>("all");
  const [groupSummaries, setGroupSummaries] = useState<GroupSummary[]>([]);
  const [groupTotal, setGroupTotal] = useState(0);
  const [groupOffset, setGroupOffset] = useState(0);
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null);
  const [selectedGroupDetail, setSelectedGroupDetail] = useState<DuplicateGroup | null>(null);
  const [failures, setFailures] = useState<ScanFailure[]>([]);
  const [failureTotal, setFailureTotal] = useState(0);
  const [failureOffset, setFailureOffset] = useState(0);
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [loadingGroupDetail, setLoadingGroupDetail] = useState(false);
  const [loadingFailures, setLoadingFailures] = useState(false);
  const [groupLoadError, setGroupLoadError] = useState<string | null>(null);
  const [groupDetailError, setGroupDetailError] = useState<string | null>(null);
  const [failureLoadError, setFailureLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const {
    actionProgress,
    clearActionProgressTimer,
    failActionProgress,
    finishActionProgress,
    finishPartialActionProgress,
    showActionProgress
  } = useActionProgress();
  const {
    handleResizeKey,
    layoutSettings,
    layoutStyle,
    resetLayoutWidth,
    resizingClass,
    reviewSurfaceRef,
    startResize,
    workspaceRef
  } = useWorkspaceLayout();
  const [activeWorkspaceTab, setActiveWorkspaceTab] = useState<WorkspaceTab>("scan");
  const [scanFormTemplate, setScanFormTemplate] = useState<ScanFormTemplate | null>(null);
  const [groupFilter, setGroupFilter] = useState<GroupFilter>("all");
  const [groupSort, setGroupSort] = useState<GroupSort>("recommended");
  const [error, setError] = useState<GlobalError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const watchScanRef = useRef<(scanId: number) => void>(() => undefined);
  const selectedScanIdRef = useRef<number | null>(null);
  const historyFilterRef = useRef<ScanHistoryFilter>("all");
  const systemRequestIdRef = useRef(0);
  const scansRequestIdRef = useRef(0);
  const groupSummariesRequestIdRef = useRef(0);
  const groupDetailRequestIdRef = useRef(0);
  const failuresRequestIdRef = useRef(0);
  const selectedGroupIdRef = useRef<number | null>(null);
  const groupFilterRef = useRef<GroupFilter>("all");
  const groupSortRef = useRef<GroupSort>("recommended");

  const setSelectedScan = useCallback((state: ScanState | null) => {
    selectedScanIdRef.current = state?.id ?? null;
    setScan(state);
  }, []);

  const setActiveGroupId = useCallback((groupId: number | null) => {
    selectedGroupIdRef.current = groupId;
    setSelectedGroupId(groupId);
  }, []);

  const setActiveGroupFilter = useCallback((filter: GroupFilter) => {
    groupFilterRef.current = filter;
    setGroupFilter(filter);
  }, []);

  const setActiveGroupSort = useCallback((sort: GroupSort) => {
    groupSortRef.current = sort;
    setGroupSort(sort);
  }, []);

  const reportGlobalError = useCallback((owner: GlobalErrorOwner, message: string) => {
    setError({ owner, message });
  }, []);

  const clearOwnedGlobalError = useCallback((owner: GlobalErrorOwner) => {
    setError((current) => (current?.owner === owner ? null : current));
  }, []);

  const clearScanDetails = useCallback(() => {
    groupSummariesRequestIdRef.current += 1;
    groupDetailRequestIdRef.current += 1;
    failuresRequestIdRef.current += 1;
    clearThumbnailCache();
    setGroupSummaries([]);
    setGroupTotal(0);
    setGroupOffset(0);
    setActiveGroupId(null);
    setSelectedGroupDetail(null);
    setFailures([]);
    setFailureTotal(0);
    setFailureOffset(0);
    setLoadingGroups(false);
    setLoadingGroupDetail(false);
    setLoadingFailures(false);
    setGroupLoadError(null);
    setGroupDetailError(null);
    setFailureLoadError(null);
  }, [setActiveGroupId]);

  const closeProgressStream = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const refreshSystem = useCallback(async (): Promise<boolean> => {
    const requestId = ++systemRequestIdRef.current;
    setSystemLoading(true);
    try {
      const response = await getSystem();
      if (systemRequestIdRef.current !== requestId) return false;
      setSystem(response);
      clearOwnedGlobalError("system");
      return true;
    } catch (err) {
      if (systemRequestIdRef.current !== requestId) return false;
      setSystem(null);
      reportGlobalError("system", messageFromError(err));
      return false;
    } finally {
      if (systemRequestIdRef.current === requestId) {
        setSystemLoading(false);
      }
    }
  }, [clearOwnedGlobalError, reportGlobalError]);

  const refreshScans = useCallback(async (
    offset = 0,
    filter: ScanHistoryFilter = historyFilterRef.current
  ): Promise<ScanState[] | null> => {
    const requestId = ++scansRequestIdRef.current;
    try {
      const status = filter === "all" ? undefined : filter as ScanStatus;
      const response = await listScans(SCAN_PAGE_SIZE, offset, status);
      if (scansRequestIdRef.current !== requestId) return null;
      setScans(response.scans);
      setScanTotal(response.total);
      setScanOffset(response.offset);
      clearOwnedGlobalError("scans");
      return response.scans;
    } catch (err) {
      if (scansRequestIdRef.current !== requestId) return null;
      const message = messageFromError(err);
      reportGlobalError("scans", message);
      return null;
    }
  }, [clearOwnedGlobalError, reportGlobalError]);

  const loadFailures = useCallback(async (scanId: number, offset = 0): Promise<boolean> => {
    const requestId = ++failuresRequestIdRef.current;
    setLoadingFailures(true);
    setFailureLoadError(null);
    try {
      const response = await getFailures(scanId, FAILURE_PAGE_SIZE, offset);
      if (
        failuresRequestIdRef.current !== requestId ||
        selectedScanIdRef.current !== scanId
      ) return false;
      setFailures(response.failures);
      setFailureTotal(response.total);
      setFailureOffset(response.offset);
      clearOwnedGlobalError("failures");
      return true;
    } catch (err) {
      if (
        failuresRequestIdRef.current !== requestId ||
        selectedScanIdRef.current !== scanId
      ) return false;
      const message = messageFromError(err);
      setFailureLoadError(message);
      reportGlobalError("failures", message);
      return false;
    } finally {
      if (
        failuresRequestIdRef.current === requestId &&
        selectedScanIdRef.current === scanId
      ) {
        setLoadingFailures(false);
      }
    }
  }, [clearOwnedGlobalError, reportGlobalError]);

  const loadGroupDetail = useCallback(
    async (scanId: number, groupId: number): Promise<DuplicateGroup | null> => {
      const requestId = ++groupDetailRequestIdRef.current;
      setLoadingGroupDetail(true);
      setGroupDetailError(null);
      try {
        const response = await getGroup(scanId, groupId);
        if (
          groupDetailRequestIdRef.current !== requestId ||
          selectedScanIdRef.current !== scanId
        ) return null;
        setActiveGroupId(groupId);
        setSelectedGroupDetail(response);
        clearOwnedGlobalError("group-detail");
        return response;
      } catch (err) {
        if (
          groupDetailRequestIdRef.current !== requestId ||
          selectedScanIdRef.current !== scanId
        ) return null;
        const message = messageFromError(err);
        setSelectedGroupDetail(null);
        setGroupDetailError(message);
        reportGlobalError("group-detail", message);
        return null;
      } finally {
        if (
          groupDetailRequestIdRef.current === requestId &&
          selectedScanIdRef.current === scanId
        ) {
          setLoadingGroupDetail(false);
        }
      }
    },
    [clearOwnedGlobalError, reportGlobalError, setActiveGroupId]
  );

  const loadGroupSummaries = useCallback(
    async (
      scanId: number,
      offset = 0,
      filter: GroupFilter = groupFilterRef.current,
      sort: GroupSort = groupSortRef.current,
      preferredGroupId: number | null = selectedGroupIdRef.current
    ): Promise<GroupSummary[] | null> => {
      const requestId = ++groupSummariesRequestIdRef.current;
      setLoadingGroups(true);
      setGroupLoadError(null);
      try {
        const response = await getGroupSummaries(scanId, GROUP_PAGE_SIZE, offset, filter, sort);
        if (
          groupSummariesRequestIdRef.current !== requestId ||
          selectedScanIdRef.current !== scanId
        ) return null;
        setGroupSummaries(response.groups);
        setGroupTotal(response.total);
        setGroupOffset(response.offset);
        clearOwnedGlobalError("group-summaries");
        const nextSelected =
          response.groups.find((group) => group.id === preferredGroupId) ??
          response.groups[0] ??
          null;
        if (!nextSelected) {
          groupDetailRequestIdRef.current += 1;
          setActiveGroupId(null);
          setSelectedGroupDetail(null);
          setGroupDetailError(null);
          setLoadingGroupDetail(false);
        } else {
          setActiveGroupId(nextSelected.id);
          void loadGroupDetail(scanId, nextSelected.id);
        }
        return response.groups;
      } catch (err) {
        if (
          groupSummariesRequestIdRef.current !== requestId ||
          selectedScanIdRef.current !== scanId
        ) return null;
        const message = messageFromError(err);
        setGroupLoadError(message);
        reportGlobalError("group-summaries", message);
        return null;
      } finally {
        if (
          groupSummariesRequestIdRef.current === requestId &&
          selectedScanIdRef.current === scanId
        ) {
          setLoadingGroups(false);
        }
      }
    },
    [clearOwnedGlobalError, loadGroupDetail, reportGlobalError, setActiveGroupId]
  );

  const handleTerminalScan = useCallback(
    async (state: ScanState) => {
      if (selectedScanIdRef.current === state.id) {
        closeProgressStream();
        if (state.status === "completed") {
          await Promise.all([loadGroupSummaries(state.id, 0), loadFailures(state.id, 0)]);
        } else {
          setGroupSummaries([]);
          setGroupTotal(0);
          setActiveGroupId(null);
          setSelectedGroupDetail(null);
          await loadFailures(state.id, 0);
        }
      }
      await refreshScans();
    },
    [closeProgressStream, loadFailures, loadGroupSummaries, refreshScans]
  );

  const watchScan = useCallback(
    (scanId: number) => {
      closeProgressStream();
      const source = scanEventSource(scanId);
      sourceRef.current = source;
      source.onmessage = (event) => {
        if (sourceRef.current !== source || selectedScanIdRef.current !== scanId) return;
        try {
          const state = JSON.parse(event.data) as ScanState;
          setSelectedScan(state);
          setScans((current) => current.map((item) => (item.id === state.id ? state : item)));
          if (!state.can_cancel) {
            void handleTerminalScan(state);
          }
        } catch (err) {
          reportGlobalError("progress", `Invalid progress event: ${messageFromError(err)}`);
        }
      };
      source.onerror = () => {
        if (sourceRef.current !== source || selectedScanIdRef.current !== scanId) return;
        source.close();
        if (sourceRef.current === source) sourceRef.current = null;
        void getScan(scanId)
          .then((state) => {
            if (selectedScanIdRef.current !== scanId) return;
            setSelectedScan(state);
            if (!state.can_cancel) {
              return handleTerminalScan(state);
            }
            reconnectTimerRef.current = window.setTimeout(() => watchScanRef.current(scanId), 1500);
          })
          .catch((err) => {
            if (selectedScanIdRef.current !== scanId) return;
            reportGlobalError("progress", `Progress stream disconnected: ${messageFromError(err)}`);
            const terminalApiError =
              err instanceof ApiError && (err.status === 401 || err.status === 404);
            if (!terminalApiError) {
              reconnectTimerRef.current = window.setTimeout(() => watchScanRef.current(scanId), 1500);
            }
          });
      };
    },
    [closeProgressStream, handleTerminalScan, reportGlobalError, setSelectedScan]
  );

  watchScanRef.current = watchScan;

  useEffect(() => {
    let cancelled = false;
    void refreshSystem();
    void refreshScans().then(async (recentScans) => {
      if (cancelled || !recentScans || recentScans.length === 0) return;
      const initial = recentScans.find((item) => item.can_cancel) ?? recentScans[0];
      setSelectedScan(initial);
      if (initial.can_cancel) {
        watchScanRef.current(initial.id);
        await loadFailures(initial.id, 0);
      } else if (initial.status === "completed") {
        await Promise.all([loadGroupSummaries(initial.id, 0), loadFailures(initial.id, 0)]);
      } else {
        await loadFailures(initial.id, 0);
      }
    });
    return () => {
      cancelled = true;
      systemRequestIdRef.current += 1;
      scansRequestIdRef.current += 1;
      groupSummariesRequestIdRef.current += 1;
      groupDetailRequestIdRef.current += 1;
      failuresRequestIdRef.current += 1;
      clearActionProgressTimer();
      closeProgressStream();
    };
  }, [
    clearActionProgressTimer,
    closeProgressStream,
    loadFailures,
    loadGroupSummaries,
    refreshScans,
    refreshSystem,
    setSelectedScan
  ]);

  async function startScan(request: ScanRequest) {
    setSubmitting(true);
    setError(null);
    setNotice(null);
    showActionProgress("Starting scan", "Creating scan job.", 0.2);
    try {
      const response = await createScan(request);
      showActionProgress("Starting scan", `Scan #${response.id} queued. Loading initial state.`, 0.55);
      const state = await getScan(response.id);
      setScanFormTemplate(null);
      setSelectedScan(state);
      clearScanDetails();
      await refreshScans();
      watchScan(state.id);
      finishActionProgress("Starting scan", `Scan #${state.id} started. Progress updates are shown below.`);
    } catch (err) {
      reportGlobalError("scan-start", messageFromError(err));
      failActionProgress("Starting scan", messageFromError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function selectScan(state: ScanState, reportProgress = true): Promise<boolean> {
    if (reportProgress) {
      showActionProgress("Loading scan details", `Loading scan #${state.id}.`, 0.2);
    }
    closeProgressStream();
    setSelectedScan(state);
    setError(null);
    setNotice(null);
    clearScanDetails();
    let groupsLoaded = true;
    let failuresLoaded = true;
    if (state.can_cancel) {
      watchScan(state.id);
      if (reportProgress && selectedScanIdRef.current === state.id) {
        showActionProgress("Loading scan details", `Loading failures for scan #${state.id}.`, 0.75);
      }
      failuresLoaded = await loadFailures(state.id, 0);
    } else if (state.status === "completed") {
      if (reportProgress && selectedScanIdRef.current === state.id) {
        showActionProgress("Loading scan details", `Loading groups and failures for scan #${state.id}.`, 0.55);
      }
      const [groupsPage, failureResult] = await Promise.all([
        loadGroupSummaries(state.id, 0),
        loadFailures(state.id, 0)
      ]);
      groupsLoaded = groupsPage !== null;
      failuresLoaded = failureResult;
    } else {
      if (reportProgress && selectedScanIdRef.current === state.id) {
        showActionProgress("Loading scan details", `Loading failures for scan #${state.id}.`, 0.75);
      }
      failuresLoaded = await loadFailures(state.id, 0);
    }
    const loaded = groupsLoaded && failuresLoaded;
    if (reportProgress && selectedScanIdRef.current === state.id) {
      if (loaded) {
        finishActionProgress("Loading scan details", `Loaded scan #${state.id}.`);
      } else {
        failActionProgress("Loading scan details", `Scan #${state.id} details could not be fully loaded.`);
      }
    }
    return loaded;
  }

  async function cancelSelectedScan(state: ScanState) {
    const wasSelected = selectedScanIdRef.current === state.id;
    setActionBusy(true);
    setError(null);
    showActionProgress("Cancelling scan", `Requesting cancellation for scan #${state.id}.`, 0.25);
    try {
      await cancelScan(state.id);
      showActionProgress("Cancelling scan", `Refreshing scan #${state.id} after cancellation request.`, 0.65);
      const updated = await getScan(state.id);
      setNotice(
        updated.can_cancel
          ? `Cancellation requested for scan #${state.id}.`
          : `Scan #${state.id} is ${updated.status}.`
      );
      if (wasSelected && selectedScanIdRef.current === state.id) {
        setSelectedScan(updated);
        if (updated.can_cancel) watchScan(updated.id);
      }
      await refreshScans();
      finishActionProgress("Cancelling scan", `Scan #${state.id} is ${updated.status}.`);
    } catch (err) {
      reportGlobalError("scan-cancel", messageFromError(err));
      failActionProgress("Cancelling scan", messageFromError(err));
    } finally {
      setActionBusy(false);
    }
  }

  async function deleteSelectedScan(state: ScanState) {
    if (!window.confirm(`Delete scan #${state.id} history? Files will not be deleted.`)) return;
    setActionBusy(true);
    setError(null);
    showActionProgress("Deleting scan history", `Deleting scan #${state.id} history.`, 0.25);
    try {
      await deleteScan(state.id);
      showActionProgress("Deleting scan history", "Refreshing scan history and selection.", 0.65);
      if (selectedScanIdRef.current === state.id) {
        closeProgressStream();
        setSelectedScan(null);
        clearScanDetails();
      }
      const nextOffset = scans.length === 1 ? Math.max(0, scanOffset - SCAN_PAGE_SIZE) : scanOffset;
      const nextPage = await refreshScans(nextOffset);
      if (selectedScanIdRef.current === null && nextPage && nextPage.length > 0) {
        const nextScan = nextPage[0];
        setSelectedScan(nextScan);
        if (nextScan.can_cancel) {
          watchScan(nextScan.id);
        } else if (nextScan.status === "completed") {
          await loadGroupSummaries(nextScan.id, 0);
        }
        await loadFailures(nextScan.id, 0);
      }
      setNotice(`Deleted scan #${state.id} history.`);
      finishActionProgress("Deleting scan history", `Deleted scan #${state.id} history.`);
    } catch (err) {
      reportGlobalError("scan-delete", messageFromError(err));
      failActionProgress("Deleting scan history", messageFromError(err));
    } finally {
      setActionBusy(false);
    }
  }

  async function resetAllAppData() {
    if (
      !window.confirm(
        "Reset all scan history and feature cache? Image files, moved duplicate folders, and the API token will not be deleted."
      )
    ) return;
    setActionBusy(true);
    setError(null);
    setNotice(null);
    showActionProgress("Resetting app data", "Clearing scan history and feature cache.", 0.35);
    try {
      const response = await resetAppData();
      scansRequestIdRef.current += 1;
      groupSummariesRequestIdRef.current += 1;
      groupDetailRequestIdRef.current += 1;
      failuresRequestIdRef.current += 1;
      clearThumbnailCache();
      closeProgressStream();
      setSelectedScan(null);
      setScans([]);
      setScanTotal(0);
      setScanOffset(0);
      clearScanDetails();
      const noticeText = resetNotice(response);
      setNotice(noticeText);
      finishActionProgress("Resetting app data", noticeText);
    } catch (err) {
      reportGlobalError("reset", messageFromError(err));
      failActionProgress("Resetting app data", messageFromError(err));
    } finally {
      setActionBusy(false);
    }
  }

  async function moveDuplicatesToFolder(groupId: number, imageIds: number[]) {
    const activeScan = scan;
    if (!activeScan) return false;
    const nextGroupOffset =
      groupSummaries.length === 1 ? Math.max(0, groupOffset - GROUP_PAGE_SIZE) : groupOffset;
    setActionBusy(true);
    setError(null);
    setNotice(null);
    showActionProgress(
      "Moving files to duplicate folder",
      `Preparing to move ${imageIds.length} file(s).`,
      0.2
    );
    try {
      showActionProgress(
        "Moving files to duplicate folder",
        `Moving ${imageIds.length} file(s) into a duplicate folder. This can take a while.`,
        0.35
      );
      const response = await moveDuplicateFiles(activeScan.id, groupId, imageIds);
      showActionProgress(
        "Moving files to duplicate folder",
        `Moved ${response.moved.length} of ${imageIds.length} file(s); refreshing result view.`,
        0.7
      );
      if (selectedScanIdRef.current === activeScan.id) {
        if (response.moved.length > 0) {
          clearThumbnailCache();
          markMovedImages(groupId, response.moved);
          await refreshSelectedScan(activeScan.id);
          await loadGroupSummaries(
            activeScan.id,
            nextGroupOffset,
            groupFilterRef.current,
            groupSortRef.current,
            groupId
          );
        } else if (response.skipped.length > 0 || response.failures.length > 0) {
          await loadGroupDetail(activeScan.id, groupId);
        }
      }
      await refreshScans(scanOffset);
      const noticeText = moveNotice(response);
      setNotice(noticeText);
      finishMoveActionProgress(response, noticeText);
      return response.moved.length > 0 && response.skipped.length === 0 && response.failures.length === 0;
    } catch (err) {
      reportGlobalError("move", messageFromError(err));
      failActionProgress("Moving files to duplicate folder", messageFromError(err));
      return false;
    } finally {
      setActionBusy(false);
    }
  }

  async function changeApiToken(value: string) {
    if (submitting || actionBusy) return;
    setActionBusy(true);
    showActionProgress("Updating API token", "Applying the local API token.", 0.4);
    try {
      closeProgressStream();
      clearThumbnailCache();
      setApiToken(value);
      setApiTokenValue(value.trim());
      setSystem(null);
      setSelectedScan(null);
      setScans([]);
      setScanTotal(0);
      setScanOffset(0);
      clearScanDetails();
      setError(null);
      setNotice(null);
      showActionProgress("Updating API token", "Verifying backend access with the new token.", 0.7);
      const [systemOk, scansPage] = await Promise.all([refreshSystem(), refreshScans()]);
      if (systemOk && scansPage !== null) {
        setNotice("Local API token updated.");
        finishActionProgress("Updating API token", "Local API token updated.");
      } else {
        failActionProgress(
          "Updating API token",
          "Token was saved locally, but backend refresh failed. Check the token and retry."
        );
      }
    } finally {
      setActionBusy(false);
    }
  }

  async function changeScanPage(offset: number) {
    showActionProgress("Loading scan history", `Loading scan history from item ${offset + 1}.`, 0.35);
    const page = await refreshScans(offset);
    if (page === null) {
      failActionProgress("Loading scan history", "Scan history could not be loaded.");
      return;
    }
    if (page.length > 0) {
      const selected = await selectScan(page[0], false);
      if (!selected) {
        failActionProgress("Loading scan history", "History loaded, but selected scan details failed.");
        return;
      }
    }
    finishActionProgress("Loading scan history", `Loaded ${page.length} scan(s).`);
  }

  async function changeHistoryFilter(filter: ScanHistoryFilter) {
    showActionProgress("Filtering scan history", `Loading ${historyFilterLabel(filter)}.`, 0.35);
    const page = await refreshScans(0, filter);
    if (page === null) {
      failActionProgress("Filtering scan history", "Scan history filter could not be applied.");
      return;
    }
    historyFilterRef.current = filter;
    setHistoryFilter(filter);
    const selectedOutsideFilter = scan !== null && filter !== "all" && scan.status !== filter;
    if (selectedOutsideFilter) {
      closeProgressStream();
      setSelectedScan(null);
      clearScanDetails();
    }
    finishActionProgress("Filtering scan history", `Loaded ${page.length} scan(s).`);
  }

  async function refreshScanHistory() {
    showActionProgress("Refreshing scan history", "Requesting latest scan history.", 0.35);
    const page = await refreshScans(scanOffset);
    if (page === null) {
      failActionProgress("Refreshing scan history", "Scan history refresh failed.");
      return;
    }
    finishActionProgress("Refreshing scan history", `Loaded ${page.length} scan(s).`);
  }

  async function refreshSystemStatus() {
    showActionProgress("Refreshing system", "Requesting backend system status.", 0.35);
    const loaded = await refreshSystem();
    if (loaded) {
      finishActionProgress("Refreshing system", "Backend system status refreshed.");
    } else {
      failActionProgress("Refreshing system", "Backend system status could not be refreshed.");
    }
  }

  async function changeGroupPage(offset: number) {
    if (!scan) return;
    showActionProgress("Loading groups", `Loading group results from item ${offset + 1}.`, 0.35);
    const page = await loadGroupSummaries(scan.id, offset);
    if (page === null) {
      failActionProgress("Loading groups", "Group results could not be loaded.");
      return;
    }
    finishActionProgress("Loading groups", `Loaded ${page.length} group(s).`);
  }

  async function changeGroupFilter(filter: GroupFilter) {
    setActiveGroupFilter(filter);
    setActiveWorkspaceTab((current) => {
      if (filter === "exact") return "exact";
      return current === "exact" ? "duplicates" : current;
    });
    groupDetailRequestIdRef.current += 1;
    setGroupSummaries([]);
    setGroupTotal(0);
    setGroupOffset(0);
    setActiveGroupId(null);
    setSelectedGroupDetail(null);
    setGroupDetailError(null);
    setLoadingGroupDetail(false);
    if (!scan || scan.status !== "completed") return;
    showActionProgress("Loading groups", "Applying group filter.", 0.35);
    const page = await loadGroupSummaries(scan.id, 0, filter, groupSortRef.current, null);
    if (page === null) {
      failActionProgress("Loading groups", "Group filter could not be applied.");
      return;
    }
    finishActionProgress("Loading groups", `Loaded ${page.length} group(s).`);
  }

  function selectWorkspaceTab(tab: WorkspaceTab) {
    setActiveWorkspaceTab(tab);
    if (tab === "duplicates" && groupFilterRef.current !== "all") {
      void changeGroupFilter("all");
    } else if (tab === "exact" && groupFilterRef.current !== "exact") {
      void changeGroupFilter("exact");
    }
  }

  function openDuplicateReview() {
    setActiveWorkspaceTab("duplicates");
    if (groupFilterRef.current !== "all") {
      void changeGroupFilter("all");
    }
  }

  function handleWorkspaceTabKey(
    event: ReactKeyboardEvent<HTMLButtonElement>,
    currentTab: WorkspaceTab
  ) {
    const currentIndex = WORKSPACE_TAB_ORDER.indexOf(currentTab);
    let nextIndex: number;
    switch (event.key) {
      case "ArrowRight":
        nextIndex = (currentIndex + 1) % WORKSPACE_TAB_ORDER.length;
        break;
      case "ArrowLeft":
        nextIndex = (currentIndex - 1 + WORKSPACE_TAB_ORDER.length) % WORKSPACE_TAB_ORDER.length;
        break;
      case "Home":
        nextIndex = 0;
        break;
      case "End":
        nextIndex = WORKSPACE_TAB_ORDER.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    const nextTab = WORKSPACE_TAB_ORDER[nextIndex];
    selectWorkspaceTab(nextTab);
    window.requestAnimationFrame(() => document.getElementById(workspaceTabId(nextTab))?.focus());
  }

  function reuseScanSettings(state: ScanState) {
    setScanFormTemplate((current) => ({
      sourceScanId: state.id,
      revision: (current?.revision ?? 0) + 1,
      request: scanRequestFromState(state)
    }));
    setActiveWorkspaceTab("scan");
    setError(null);
    setNotice(null);
  }

  async function changeGroupSort(sort: GroupSort) {
    setActiveGroupSort(sort);
    if (!scan || scan.status !== "completed") return;
    showActionProgress("Loading groups", "Applying group sort.", 0.35);
    const page = await loadGroupSummaries(scan.id, 0, groupFilterRef.current, sort, selectedGroupId);
    if (page === null) {
      failActionProgress("Loading groups", "Group sort could not be applied.");
      return;
    }
    finishActionProgress("Loading groups", `Loaded ${page.length} group(s).`);
  }

  async function selectGroupSummary(group: GroupSummary) {
    const activeScan = scan;
    if (!activeScan) return;
    setActiveGroupId(group.id);
    setSelectedGroupDetail(null);
    setGroupDetailError(null);
    await loadGroupDetail(activeScan.id, group.id);
  }

  async function changeFailurePage(offset: number) {
    if (!scan) return;
    showActionProgress("Loading failures", `Loading recorded issues from item ${offset + 1}.`, 0.35);
    const loaded = await loadFailures(scan.id, offset);
    if (loaded) {
      finishActionProgress("Loading failures", "Recorded issues loaded.");
    } else {
      failActionProgress("Loading failures", "Recorded issues could not be loaded.");
    }
  }

  async function refreshSelectedScan(scanId: number) {
    try {
      const updated = await getScan(scanId);
      if (selectedScanIdRef.current !== scanId) return;
      setSelectedScan(updated);
      setScans((current) => current.map((item) => (item.id === scanId ? updated : item)));
    } catch (err) {
      if (selectedScanIdRef.current === scanId) {
        reportGlobalError(
          "scan-refresh",
          `Files were moved, but scan refresh failed: ${messageFromError(err)}`
        );
      }
    }
  }

  function markMovedImages(groupId: number, imageIds: number[]) {
    if (imageIds.length === 0) return;
    const movedIds = new Set(imageIds);
    setGroupSummaries((current) =>
      current.map((group) =>
        group.id === groupId
          ? {
              ...group,
              moved_count: Math.min(group.image_count, group.moved_count + imageIds.length)
            }
          : group
      )
    );
    setSelectedGroupDetail((current) =>
      current ? markGroupImagesAsMoved(current, movedIds) : current
    );
  }

  function finishMoveActionProgress(response: MoveDuplicatesResponse, detail: string) {
    if (response.moved.length === 0) {
      failActionProgress("Moving files to duplicate folder", detail);
    } else if (response.skipped.length > 0 || response.failures.length > 0) {
      finishPartialActionProgress("Moving files to duplicate folder", detail);
    } else {
      finishActionProgress("Moving files to duplicate folder", detail);
    }
  }

  const warningCount = failureTotal + (scan?.warnings.length ?? 0);

  return (
    <main className={`app-shell${resizingClass}`} data-ui-version="minimal-20261014">
      <a className="skip-link" href={`#${workspaceTabPanelId(activeWorkspaceTab)}`}>ワークスペースへ移動</a>
      <header className="ledger-topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <ScanSearch size={25} strokeWidth={1.5} />
          </div>
          <div>
            <h1>AntiDupeImage</h1>
          </div>
        </div>

        <div className="topbar-status" aria-label="Local safety status">
          <Lock size={14} />
          <span>LOCAL ONLY</span>
          <span className={`connection-state ${system ? "connected" : ""}`}>
            {systemLoading ? "接続を確認中" : system ? "接続済み" : "未接続"}
          </span>
        </div>

        <button
          className="secondary-button topbar-action"
          disabled={systemLoading || actionBusy || submitting}
          onClick={() => {
            void refreshSystem();
            void refreshScans(scanOffset);
          }}
        >
          <RefreshCw size={17} className={systemLoading ? "spin" : ""} />
          <span>Refresh</span>
        </button>
      </header>

      {error ? <div className="global-error" role="alert">{error.message}</div> : null}
      {notice ? <div className="global-notice" role="status">{notice}</div> : null}
      {actionProgress ? <ActionProgressBanner progress={actionProgress} /> : null}

      <div className="ledger-workspace" style={layoutStyle} ref={workspaceRef}>
        <nav className="workspace-tabs" aria-label="Workspace tabs" role="tablist">
          <WorkspaceTabButton
            active={activeWorkspaceTab === "overview"}
            icon={<Database size={18} />}
            label="Overview"
            tab="overview"
            onClick={() => selectWorkspaceTab("overview")}
            onKeyDown={(event) => handleWorkspaceTabKey(event, "overview")}
          />
          <WorkspaceTabButton
            active={activeWorkspaceTab === "scan"}
            icon={<FolderInput size={18} />}
            label="Scan"
            tab="scan"
            onClick={() => selectWorkspaceTab("scan")}
            onKeyDown={(event) => handleWorkspaceTabKey(event, "scan")}
          />
          <WorkspaceTabButton
            active={activeWorkspaceTab === "duplicates"}
            icon={<Images size={18} />}
            label="Duplicates"
            count={scan?.duplicate_groups ?? 0}
            tab="duplicates"
            onClick={() => selectWorkspaceTab("duplicates")}
            onKeyDown={(event) => handleWorkspaceTabKey(event, "duplicates")}
          />
          <WorkspaceTabButton
            active={activeWorkspaceTab === "exact"}
            icon={<FileImage size={18} />}
            label="Exact matches"
            tab="exact"
            onClick={() => selectWorkspaceTab("exact")}
            onKeyDown={(event) => handleWorkspaceTabKey(event, "exact")}
          />
          <WorkspaceTabButton
            active={activeWorkspaceTab === "warnings"}
            icon={<AlertTriangle size={18} />}
            label="Warnings"
            count={warningCount}
            tab="warnings"
            onClick={() => selectWorkspaceTab("warnings")}
            onKeyDown={(event) => handleWorkspaceTabKey(event, "warnings")}
          />
        </nav>

        <section
          className={`tab-panel tab-panel-${activeWorkspaceTab}`}
          id={workspaceTabPanelId(activeWorkspaceTab)}
          role="tabpanel"
          aria-labelledby={workspaceTabId(activeWorkspaceTab)}
          tabIndex={-1}
        >
          {activeWorkspaceTab === "overview" ? (
            <div className="overview-layout">
              <SnapshotStrip
                scan={scan}
                groupTotal={groupTotal}
                failureTotal={failureTotal}
              />
              <ProgressPanel scan={scan} onViewDuplicates={openDuplicateReview} />
            </div>
          ) : null}

          {activeWorkspaceTab === "scan" ? (
            <div className="operations-grid">
              <div className="operations-primary">
                <ScanForm
                  system={system}
                  busy={submitting || actionBusy}
                  template={scanFormTemplate}
                  onSubmit={startScan}
                />
              </div>
              <ProgressPanel scan={scan} onViewDuplicates={openDuplicateReview} />
              <SystemStatus
                system={system}
                loading={systemLoading}
                apiToken={apiTokenValue}
                resetBusy={actionBusy || submitting}
                onApiTokenChange={changeApiToken}
                onRefresh={() => void refreshSystemStatus()}
                onReset={() => void resetAllAppData()}
              />
              <ScanHistory
                scans={scans}
                total={scanTotal}
                limit={SCAN_PAGE_SIZE}
                offset={scanOffset}
                selectedId={scan?.id}
                filter={historyFilter}
                busy={actionBusy || submitting}
                onSelect={selectScan}
                onReuse={reuseScanSettings}
                onCancel={cancelSelectedScan}
                onDelete={deleteSelectedScan}
                onRefresh={() => void refreshScanHistory()}
                onPage={(offset) => void changeScanPage(offset)}
                onFilter={(filter) => void changeHistoryFilter(filter)}
              />
            </div>
          ) : null}

          {activeWorkspaceTab === "duplicates" || activeWorkspaceTab === "exact" ? (
            <>
              <div className="review-surface" ref={reviewSurfaceRef}>
                <GroupList
                  groups={groupSummaries}
                  total={groupTotal}
                  limit={GROUP_PAGE_SIZE}
                  offset={groupOffset}
                  selectedId={selectedGroupId ?? undefined}
                  filter={groupFilter}
                  sort={groupSort}
                  loading={loadingGroups}
                  error={groupLoadError}
                  onFilterChange={(filter) => void changeGroupFilter(filter)}
                  onSortChange={(sort) => void changeGroupSort(sort)}
                  onSelect={(group) => void selectGroupSummary(group)}
                  onPage={(offset) => void changeGroupPage(offset)}
                  onRetry={() => {
                    if (scan) void loadGroupSummaries(scan.id, groupOffset);
                  }}
                />
                <ResizeHandle
                  className="review-resizer"
                  label="Resize compare inspector"
                  orientation="vertical"
                  value={layoutSettings.comparePanel}
                  min={COMPARE_PANEL_WIDTH.min}
                  max={COMPARE_PANEL_WIDTH.max}
                  onPointerDown={(event) => startResize("comparePanel", event)}
                  onDoubleClick={() => resetLayoutWidth("comparePanel")}
                  onKeyDown={(event) => handleResizeKey(event, "comparePanel", "ArrowRight", "ArrowLeft")}
                />
                <ImageCompare
                  group={selectedGroupDetail}
                  loading={loadingGroupDetail}
                  error={groupDetailError}
                  onRetry={() => {
                    if (scan && selectedGroupId !== null) void loadGroupDetail(scan.id, selectedGroupId);
                  }}
                  onMove={moveDuplicatesToFolder}
                  busy={actionBusy}
                />
              </div>
              <ResizeHandle
                className="main-section-resizer review-surface-resizer"
                label="Resize review area"
                orientation="horizontal"
                value={layoutSettings.reviewSurface}
                min={REVIEW_SURFACE_HEIGHT.min}
                max={REVIEW_SURFACE_HEIGHT.max}
                onPointerDown={(event) => startResize("reviewSurface", event)}
                onDoubleClick={() => resetLayoutWidth("reviewSurface")}
                onKeyDown={(event) => handleResizeKey(event, "reviewSurface", "ArrowUp", "ArrowDown")}
              />
            </>
          ) : null}

          {activeWorkspaceTab === "warnings" ? (
            <div className="warnings-layout">
              <ProgressPanel scan={scan} onViewDuplicates={openDuplicateReview} />
              {warningCount === 0 && !loadingFailures && !failureLoadError ? (
                <section className="panel empty-tab-state" aria-label="Warning status">
                  <CheckCircle2 size={32} />
                  <strong>No warnings or recorded issues</strong>
                </section>
              ) : null}
              <FailureList
                failures={failures}
                total={failureTotal}
                limit={FAILURE_PAGE_SIZE}
                offset={failureOffset}
                loading={loadingFailures}
                error={failureLoadError}
                onRetry={() => {
                  if (scan) void loadFailures(scan.id, failureOffset);
                }}
                onPage={(offset) => void changeFailurePage(offset)}
                onReuseSettings={scan ? () => reuseScanSettings(scan) : undefined}
              />
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}

function ResizeHandle({
  className,
  label,
  orientation,
  value,
  min,
  max,
  onPointerDown,
  onDoubleClick,
  onKeyDown
}: {
  className: string;
  label: string;
  orientation: "horizontal" | "vertical";
  value: number;
  min: number;
  max: number;
  onPointerDown: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onDoubleClick: () => void;
  onKeyDown: (event: ReactKeyboardEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button
      type="button"
      className={`resize-handle resize-handle-${orientation} ${className}`}
      role="separator"
      aria-label={label}
      aria-orientation={orientation}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      title={label}
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
      onKeyDown={onKeyDown}
    >
      <span aria-hidden="true" />
    </button>
  );
}

function WorkspaceTabButton({
  icon,
  label,
  tab,
  onClick,
  onKeyDown,
  active = false,
  count
}: {
  icon: ReactNode;
  label: string;
  tab: WorkspaceTab;
  onClick: () => void;
  onKeyDown: (event: ReactKeyboardEvent<HTMLButtonElement>) => void;
  active?: boolean;
  count?: number;
}) {
  return (
    <button
      className={`workspace-tab ${active ? "active" : ""}`}
      type="button"
      id={workspaceTabId(tab)}
      role="tab"
      aria-controls={workspaceTabPanelId(tab)}
      aria-selected={active}
      aria-label={typeof count === "number" && count > 0 ? `${label} ${formatCount(count)}` : label}
      tabIndex={active ? 0 : -1}
      onClick={onClick}
      onKeyDown={onKeyDown}
    >
      {icon}
      <span>{label}</span>
      {typeof count === "number" && count > 0 ? <strong className="tab-count">{formatCount(count)}</strong> : null}
    </button>
  );
}

function workspaceTabId(tab: WorkspaceTab): string {
  return `workspace-tab-${tab}`;
}

function workspaceTabPanelId(tab: WorkspaceTab): string {
  return `workspace-tab-panel-${tab}`;
}

function SnapshotStrip({
  scan,
  groupTotal,
  failureTotal
}: {
  scan: ScanState | null;
  groupTotal: number;
  failureTotal: number;
}) {
  const selected = scan?.status === "completed" ? "Completed" : scan?.status ?? "No scan";
  const duplicateGroupTotal = scan?.duplicate_groups ?? 0;
  return (
    <section className="snapshot-strip" aria-label="Scan snapshot summary">
      <SummaryCard
        icon={<FolderLock size={21} />}
        label="Scope"
        value={scan?.path ?? "No folder selected"}
      />
      <SummaryCard
        icon={<CheckCircle2 size={21} />}
        label="Scan snapshot"
        value={selected}
      />
      <SummaryCard
        icon={<Images size={21} />}
        label="Duplicate groups"
        value={formatCount(duplicateGroupTotal)}
      />
      <SummaryCard
        icon={<FileImage size={21} />}
        label="Filtered review groups"
        value={formatCount(groupTotal)}
      />
      <SummaryCard
        icon={<AlertTriangle size={21} />}
        label="Warnings"
        value={formatCount(failureTotal + (scan?.warnings.length ?? 0))}
      />
    </section>
  );
}

function SummaryCard({
  icon,
  label,
  value
}: {
  icon: ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="summary-card">
      <div className="summary-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong title={value}>{value}</strong>
      </div>
    </div>
  );
}

function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function messageFromError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function scanRequestFromState(state: ScanState): Omit<ScanRequest, "allow_clip_fallback"> {
  const cudaDevice = /^cuda:(\d+)$/.exec(state.device);
  return {
    path: state.path,
    recursive: state.recursive,
    model: state.model || undefined,
    pretrained: state.pretrained || undefined,
    device: cudaDevice ? "cuda" : state.device,
    gpu_index: cudaDevice ? Number(cudaDevice[1]) : null,
    thresholds: { ...state.thresholds }
  };
}

function ActionProgressBanner({ progress }: { progress: ActionProgress }) {
  const percent = Math.round(progress.progress * 100);
  return (
    <section
      className={`action-progress ${progress.status}`}
      role="status"
      aria-live="polite"
      aria-label={`${progress.label}: ${progress.detail}`}
    >
      <div className="action-progress-header">
        <strong>{progress.label}</strong>
        <span>{progress.status === "running" ? `${percent}%` : progress.status}</span>
      </div>
      <div className="action-progress-bar" aria-hidden="true">
        <div style={{ width: `${percent}%` }} />
      </div>
      <p>{progress.detail}</p>
    </section>
  );
}

function markGroupImagesAsMoved(group: DuplicateGroup, movedIds: Set<number>): DuplicateGroup {
  return {
    ...group,
    images: group.images.map((image) =>
      movedIds.has(image.id)
        ? {
            ...image,
            moved: true,
            trashed: true,
            status: "moved",
            available: false
          }
        : image
    )
  };
}

function moveNotice(response: MoveDuplicatesResponse): string {
  const movedCount = response.moved.length;
  const parts = [
    movedCount === 0
      ? "No files were moved to the duplicate folder."
      : `Moved ${formatCount(movedCount)} ${movedCount === 1 ? "file" : "files"} to duplicate folder.`
  ];
  if (response.destination_folder) {
    parts.push(`Destination: ${response.destination_folder}`);
  }
  if (response.skipped.length > 0) {
    const skippedCount = response.skipped.length;
    parts.push(`Skipped ${formatCount(skippedCount)} ${skippedCount === 1 ? "file" : "files"}.`);
  }
  if (response.failures.length > 0) {
    const failureText = response.failures
      .map((failure) => `#${failure.image_id}: ${failure.reason}`)
      .join("; ");
    parts.push(`Failures: ${failureText}`);
  }
  return parts.join(" ");
}

function historyFilterLabel(filter: ScanHistoryFilter): string {
  if (filter === "all") return "all scans";
  return `${filter} scans`;
}

function resetNotice(response: ResetAppDataResponse): string {
  return (
    `Reset app data. Removed ${response.scans_deleted} scan(s), ` +
    `${response.scan_images_deleted} scan image snapshot(s), ` +
    `${response.groups_deleted} duplicate group(s), ` +
    `${response.failures_deleted} failure record(s), and ` +
    `${response.cached_images_deleted} cached image feature row(s). Image files were not deleted.`
  );
}
