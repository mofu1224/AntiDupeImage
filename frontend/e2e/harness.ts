export interface OwnedBackendEnvironmentOptions {
  parentEnvironment: NodeJS.ProcessEnv;
  apiToken: string;
  appHome: string;
  handoffPath: string;
  launchNonce: string;
  uiOrigin: string;
  uvCacheDir: string;
  pythonEnvironment: Readonly<Record<string, string>>;
}

export function stripApplicationEnvironment(
  parentEnvironment: NodeJS.ProcessEnv
): NodeJS.ProcessEnv {
  return Object.fromEntries(
    Object.entries(parentEnvironment).filter(
      ([name]) => !name.toUpperCase().startsWith("ANTIDUPEIMAGE_")
    )
  );
}

export function buildOwnedBackendEnvironment(
  options: OwnedBackendEnvironmentOptions
): NodeJS.ProcessEnv {
  return {
    ...stripApplicationEnvironment(options.parentEnvironment),
    ANTIDUPEIMAGE_API_TOKEN: options.apiToken,
    ANTIDUPEIMAGE_HOME: options.appHome,
    ANTIDUPEIMAGE_HANDOFF_PATH: options.handoffPath,
    ANTIDUPEIMAGE_LAUNCH_NONCE: options.launchNonce,
    ANTIDUPEIMAGE_UI_ORIGIN: options.uiOrigin,
    UV_CACHE_DIR: options.uvCacheDir,
    ...options.pythonEnvironment,
  };
}
