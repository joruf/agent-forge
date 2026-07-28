import { useResizableModalSize } from "./useResizableModalSize";

const STORAGE_KEY = "agentforge-settings-modal-size";
const DEFAULT_WIDTH = 560;
const DEFAULT_HEIGHT = 640;
const MIN_WIDTH = 420;
const MIN_HEIGHT = 320;
const MAX_WIDTH = 1400;

/**
 * Persist settings modal size between sessions.
 *
 * @param open Whether the settings modal is visible
 * @return Modal ref and inline size style
 */
export function useSettingsModalSize(open: boolean) {
  return useResizableModalSize(open, {
    storageKey: STORAGE_KEY,
    defaultWidth: DEFAULT_WIDTH,
    defaultHeight: DEFAULT_HEIGHT,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    maxWidth: MAX_WIDTH,
  });
}

export type { ResizableModalSize as SettingsModalSize } from "./useResizableModalSize";
