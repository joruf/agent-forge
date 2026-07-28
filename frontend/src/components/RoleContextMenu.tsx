import { useEffect, useRef } from "react";
import { useI18n } from "../hooks/useI18n";

interface RoleContextMenuProps {
  x: number;
  y: number;
  onViewEdit: () => void;
  onClose: () => void;
}

/**
 * Small right-click context menu shown over a role chip, offering to open
 * the role details/edit dialog.
 */
export function RoleContextMenu({ x, y, onViewEdit, onClose }: RoleContextMenuProps) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onClose();
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  return (
    <div ref={ref} className="role-context-menu" style={{ left: x, top: y }}>
      <button type="button" className="role-context-menu-item" onClick={onViewEdit}>
        {t("chat.roleContextMenu.viewEdit")}
      </button>
    </div>
  );
}
