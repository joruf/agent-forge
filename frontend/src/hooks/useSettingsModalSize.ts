import { useEffect, useRef, useState, type CSSProperties } from "react";

const STORAGE_KEY = "agentforge-settings-modal-size";
const DEFAULT_WIDTH = 560;
const DEFAULT_HEIGHT = 640;
const MIN_WIDTH = 420;
const MIN_HEIGHT = 320;
const MAX_WIDTH = 1400;

export interface SettingsModalSize {
  width: number;
  height: number;
}

function clampSize(width: number, height: number): SettingsModalSize {
  const maxW = Math.min(window.innerWidth * 0.95, MAX_WIDTH);
  const maxH = window.innerHeight * 0.95;
  return {
    width: Math.round(Math.min(maxW, Math.max(MIN_WIDTH, width))),
    height: Math.round(Math.min(maxH, Math.max(MIN_HEIGHT, height))),
  };
}

function readStoredSize(): SettingsModalSize {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return clampSize(DEFAULT_WIDTH, DEFAULT_HEIGHT);
    }
    const parsed = JSON.parse(raw) as Partial<SettingsModalSize>;
    if (typeof parsed.width !== "number" || typeof parsed.height !== "number") {
      return clampSize(DEFAULT_WIDTH, DEFAULT_HEIGHT);
    }
    return clampSize(parsed.width, parsed.height);
  } catch {
    return clampSize(DEFAULT_WIDTH, DEFAULT_HEIGHT);
  }
}

export function useSettingsModalSize(open: boolean) {
  const modalRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<SettingsModalSize>(() => readStoredSize());

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    setSize(readStoredSize());

    const node = modalRef.current;
    if (!node) {
      return undefined;
    }

    let saveTimeout: number | undefined;
    const persistSize = () => {
      const next = clampSize(node.offsetWidth, node.offsetHeight);
      setSize(next);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    };

    const observer = new ResizeObserver(() => {
      if (saveTimeout !== undefined) {
        window.clearTimeout(saveTimeout);
      }
      saveTimeout = window.setTimeout(persistSize, 120);
    });
    observer.observe(node);

    return () => {
      observer.disconnect();
      if (saveTimeout !== undefined) {
        window.clearTimeout(saveTimeout);
      }
    };
  }, [open]);

  return {
    modalRef,
    modalSizeStyle: {
      width: `${size.width}px`,
      height: `${size.height}px`,
    } satisfies CSSProperties,
  };
}
