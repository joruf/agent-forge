import { useCallback, useState } from "react";

const STORAGE_KEY = "agentforge-sidebar-width";
const MIN_WIDTH = 220;
const MAX_WIDTH = 520;
const DEFAULT_WIDTH = 280;

function readStoredWidth(): number {
  const stored = localStorage.getItem(STORAGE_KEY);
  const parsed = stored ? Number(stored) : DEFAULT_WIDTH;
  if (!Number.isFinite(parsed)) {
    return DEFAULT_WIDTH;
  }
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, parsed));
}

export function useSidebarResize() {
  const [width, setWidth] = useState(readStoredWidth);

  const startResize = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    document.body.classList.add("sidebar-resizing");

    const onMove = (moveEvent: MouseEvent) => {
      const next = Math.min(
        MAX_WIDTH,
        Math.max(MIN_WIDTH, startWidth + moveEvent.clientX - startX),
      );
      setWidth(next);
    };

    const onUp = (upEvent: MouseEvent) => {
      const next = Math.min(
        MAX_WIDTH,
        Math.max(MIN_WIDTH, startWidth + upEvent.clientX - startX),
      );
      setWidth(next);
      localStorage.setItem(STORAGE_KEY, String(Math.round(next)));
      document.body.classList.remove("sidebar-resizing");
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [width]);

  return { width, startResize };
}
