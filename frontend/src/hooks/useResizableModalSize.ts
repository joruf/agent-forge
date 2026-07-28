import { useEffect, useRef, useState, type CSSProperties } from "react";

export interface ResizableModalSize {
  width: number;
  height: number;
}

export interface ResizableModalSizeOptions {
  storageKey: string;
  defaultWidth: number;
  defaultHeight: number;
  minWidth?: number;
  minHeight?: number;
  maxWidth?: number;
}

const DEFAULT_MIN_WIDTH = 420;
const DEFAULT_MIN_HEIGHT = 320;
const DEFAULT_MAX_WIDTH = 1400;

function clampSize(
  width: number,
  height: number,
  options: ResizableModalSizeOptions,
): ResizableModalSize {
  const maxW = Math.min(window.innerWidth * 0.95, options.maxWidth ?? DEFAULT_MAX_WIDTH);
  const maxH = window.innerHeight * 0.95;
  const minW = options.minWidth ?? DEFAULT_MIN_WIDTH;
  const minH = options.minHeight ?? DEFAULT_MIN_HEIGHT;
  return {
    width: Math.round(Math.min(maxW, Math.max(minW, width))),
    height: Math.round(Math.min(maxH, Math.max(minH, height))),
  };
}

function readStoredSize(options: ResizableModalSizeOptions): ResizableModalSize {
  try {
    const raw = localStorage.getItem(options.storageKey);
    if (!raw) {
      return clampSize(options.defaultWidth, options.defaultHeight, options);
    }
    const parsed = JSON.parse(raw) as Partial<ResizableModalSize>;
    if (typeof parsed.width !== "number" || typeof parsed.height !== "number") {
      return clampSize(options.defaultWidth, options.defaultHeight, options);
    }
    return clampSize(parsed.width, parsed.height, options);
  } catch {
    return clampSize(options.defaultWidth, options.defaultHeight, options);
  }
}

/**
 * Persist and restore a resizable modal size via ResizeObserver.
 *
 * @param open Whether the modal is visible
 * @param options Size defaults and storage key
 * @return Modal ref and inline size style
 */
export function useResizableModalSize(open: boolean, options: ResizableModalSizeOptions) {
  const modalRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<ResizableModalSize>(() => readStoredSize(options));

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    setSize(readStoredSize(options));

    const node = modalRef.current;
    if (!node) {
      return undefined;
    }

    let saveTimeout: number | undefined;
    const persistSize = () => {
      const next = clampSize(node.offsetWidth, node.offsetHeight, options);
      setSize(next);
      localStorage.setItem(options.storageKey, JSON.stringify(next));
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
  }, [open, options.storageKey, options.defaultWidth, options.defaultHeight, options.minWidth, options.minHeight, options.maxWidth]);

  return {
    modalRef,
    modalSizeStyle: {
      width: `${size.width}px`,
      height: `${size.height}px`,
    } satisfies CSSProperties,
  };
}
