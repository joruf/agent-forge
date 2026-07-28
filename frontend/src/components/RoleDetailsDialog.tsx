import { useEffect, useState } from "react";
import type { AgentRole } from "../types";
import { api } from "../services/api";
import { useI18n } from "../hooks/useI18n";
import { useEscapeClose } from "../hooks/useEscapeClose";
import { parseApiError, validateRoleForm, type RoleFormValues } from "../utils/roleForm";

interface RoleDetailsDialogProps {
  role: AgentRole | null;
  onClose: () => void;
  onSaved: (role: AgentRole) => void;
}

/**
 * Dialog to view and edit a role's description and system prompt, opened
 * from the role context menu in the chat header.
 */
export function RoleDetailsDialog({ role, onClose, onSaved }: RoleDetailsDialogProps) {
  const { t } = useI18n();
  const [form, setForm] = useState<RoleFormValues>({ id: "", name: "", description: "", system_prompt: "" });
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof RoleFormValues, string>>>({});
  const [apiError, setApiError] = useState("");
  const [busy, setBusy] = useState(false);

  useEscapeClose(role !== null, onClose);

  useEffect(() => {
    if (!role) {
      return;
    }
    setForm({
      id: role.id,
      name: role.name,
      description: role.description,
      system_prompt: role.system_prompt,
    });
    setFieldErrors({});
    setApiError("");
    setBusy(false);
  }, [role]);

  if (!role) {
    return null;
  }

  const updateField = (field: keyof RoleFormValues, value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => {
      if (!current[field]) {
        return current;
      }
      const next = { ...current };
      delete next[field];
      return next;
    });
  };

  const handleSave = async () => {
    const validationErrors = validateRoleForm(form, { isCreate: false });
    if (validationErrors.length > 0) {
      const nextErrors: Partial<Record<keyof RoleFormValues, string>> = {};
      for (const error of validationErrors) {
        nextErrors[error.field] = t(error.messageKey);
      }
      setFieldErrors(nextErrors);
      return;
    }

    setBusy(true);
    setApiError("");
    try {
      const updated = await api.updateRole(role.id, {
        name: form.name.trim(),
        description: form.description.trim(),
        system_prompt: form.system_prompt.trim(),
      });
      onSaved(updated);
      onClose();
    } catch (error) {
      setApiError(parseApiError(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal role-details-modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header role-details-modal-header">
          <h2>{t("chat.roleDetailsDialog.title")}</h2>
          <span className={`role-badge role-badge--${role.is_builtin ? "builtin" : "custom"}`}>
            {t(role.is_builtin ? "chat.roleDetailsDialog.builtinBadge" : "chat.roleDetailsDialog.customBadge")}
          </span>
        </div>
        <div className="modal-body role-details-body">
          <div className="role-details-fields">
            {role.is_builtin && <p className="roles-editor-hint">{t("chat.roleDetailsDialog.builtinNote")}</p>}
            {apiError && <p className="setup-error">{apiError}</p>}
            <label>
              {t("chat.roleDetailsDialog.roleId")}
              <input value={form.id} disabled />
            </label>
            <label>
              {t("settings.roles.name")}
              <input
                value={form.name}
                onChange={(event) => updateField("name", event.target.value)}
                disabled={busy}
              />
              {fieldErrors.name && <span className="field-error">{fieldErrors.name}</span>}
            </label>
            <label>
              {t("settings.roles.description")}
              <input
                value={form.description}
                onChange={(event) => updateField("description", event.target.value)}
                disabled={busy}
              />
              {fieldErrors.description && <span className="field-error">{fieldErrors.description}</span>}
            </label>
          </div>
          <label className="role-details-prompt-field">
            {t("settings.roles.systemPrompt")}
            <textarea
              value={form.system_prompt}
              onChange={(event) => updateField("system_prompt", event.target.value)}
              disabled={busy}
            />
            {fieldErrors.system_prompt && <span className="field-error">{fieldErrors.system_prompt}</span>}
          </label>
        </div>
        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button type="button" className="btn-primary" onClick={() => void handleSave()} disabled={busy}>
            {busy ? t("settings.roles.saving") : t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
