import { useMemo, useState } from "react";
import type { AgentRole } from "../types";
import { api } from "../services/api";
import { sortSdlcRoles } from "../constants/roles";
import { useI18n } from "../hooks/useI18n";
import {
  isBuiltinRole,
  parseApiError,
  partitionRoles,
  type RoleFormValues,
  validateRoleForm,
} from "../utils/roleForm";

interface RoleEditorPanelProps {
  roles: AgentRole[];
  onRolesChanged: (roles: AgentRole[]) => void;
}

type EditorMode = "idle" | "create" | "edit";

const EMPTY_FORM: RoleFormValues = {
  id: "",
  name: "",
  description: "",
  system_prompt: "",
};

export function RoleEditorPanel({ roles, onRolesChanged }: RoleEditorPanelProps) {
  const { t } = useI18n();
  const [mode, setMode] = useState<EditorMode>("idle");
  const [editingRoleId, setEditingRoleId] = useState<string | null>(null);
  const [form, setForm] = useState<RoleFormValues>(EMPTY_FORM);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof RoleFormValues, string>>>({});
  const [apiError, setApiError] = useState("");
  const [busy, setBusy] = useState(false);

  const { builtin, custom } = useMemo(() => {
    const partitioned = partitionRoles(roles);
    return {
      builtin: sortSdlcRoles(partitioned.builtin),
      custom: [...partitioned.custom].sort((a, b) => a.name.localeCompare(b.name)),
    };
  }, [roles]);

  const existingCustomIds = custom.map((role) => role.id);

  const resetForm = () => {
    setMode("idle");
    setEditingRoleId(null);
    setForm(EMPTY_FORM);
    setFieldErrors({});
    setApiError("");
  };

  const refreshRoles = async () => {
    const nextRoles = await api.listRoles();
    onRolesChanged(nextRoles);
    return nextRoles;
  };

  const startCreate = () => {
    setMode("create");
    setEditingRoleId(null);
    setForm(EMPTY_FORM);
    setFieldErrors({});
    setApiError("");
  };

  const startEdit = (role: AgentRole) => {
    setMode("edit");
    setEditingRoleId(role.id);
    setForm({
      id: role.id,
      name: role.name,
      description: role.description,
      system_prompt: role.system_prompt,
    });
    setFieldErrors({});
    setApiError("");
  };

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
    const validationErrors = validateRoleForm(form, {
      isCreate: mode === "create",
      existingIds: existingCustomIds,
    });
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
      if (mode === "create") {
        await api.createRole({
          id: form.id.trim(),
          name: form.name.trim(),
          description: form.description.trim(),
          system_prompt: form.system_prompt.trim(),
        });
      } else if (editingRoleId) {
        await api.updateRole(editingRoleId, {
          name: form.name.trim(),
          description: form.description.trim(),
          system_prompt: form.system_prompt.trim(),
        });
      }
      await refreshRoles();
      resetForm();
    } catch (error) {
      setApiError(parseApiError(error));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (role: AgentRole) => {
    if (isBuiltinRole(role)) {
      return;
    }
    const confirmed = window.confirm(
      t("settings.roles.deleteConfirm", { name: role.name }),
    );
    if (!confirmed) {
      return;
    }

    setBusy(true);
    setApiError("");
    try {
      await api.deleteRole(role.id);
      if (editingRoleId === role.id) {
        resetForm();
      }
      await refreshRoles();
    } catch (error) {
      setApiError(parseApiError(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="roles-editor">
      <div className="roles-editor-header">
        <div>
          <h4>{t("settings.roles.title", { count: roles.length })}</h4>
          <p className="roles-editor-hint">{t("settings.roles.hint")}</p>
        </div>
        {mode === "idle" && (
          <button type="button" className="btn-primary" onClick={startCreate}>
            {t("settings.roles.create")}
          </button>
        )}
      </div>

      {apiError && <p className="setup-error">{apiError}</p>}

      {mode !== "idle" && (
        <div className="role-form-card">
          <h5>{mode === "create" ? t("settings.roles.createTitle") : t("settings.roles.editTitle")}</h5>
          {mode === "create" && (
            <label>
              {t("settings.roles.id")}
              <input
                value={form.id}
                onChange={(event) => updateField("id", event.target.value)}
                placeholder={t("settings.roles.idPlaceholder")}
                spellCheck={false}
                disabled={busy}
              />
              {fieldErrors.id && <span className="field-error">{fieldErrors.id}</span>}
              <small className="field-hint">{t("settings.roles.idHint")}</small>
            </label>
          )}
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
          <label>
            {t("settings.roles.systemPrompt")}
            <textarea
              value={form.system_prompt}
              onChange={(event) => updateField("system_prompt", event.target.value)}
              rows={6}
              disabled={busy}
            />
            {fieldErrors.system_prompt && <span className="field-error">{fieldErrors.system_prompt}</span>}
          </label>
          <div className="role-form-actions">
            <button type="button" onClick={resetForm} disabled={busy}>
              {t("common.cancel")}
            </button>
            <button type="button" className="btn-primary" onClick={() => void handleSave()} disabled={busy}>
              {busy ? t("settings.roles.saving") : t("common.save")}
            </button>
          </div>
        </div>
      )}

      <div className="roles-section">
        <h5>{t("settings.roles.builtinSection", { count: builtin.length })}</h5>
        <ul className="roles-list">
          {builtin.map((role) => (
            <li key={role.id} className="role-card role-card--builtin">
              <div className="role-card-main">
                <strong>{role.name}</strong>
                <span className="role-badge role-badge--builtin">{t("settings.roles.builtinBadge")}</span>
                <p>{role.description}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="roles-section">
        <h5>{t("settings.roles.customSection", { count: custom.length })}</h5>
        {custom.length === 0 ? (
          <p className="roles-empty">{t("settings.roles.noCustom")}</p>
        ) : (
          <ul className="roles-list">
            {custom.map((role) => (
              <li key={role.id} className="role-card role-card--custom">
                <div className="role-card-main">
                  <strong>{role.name}</strong>
                  <span className="role-badge role-badge--custom">{t("settings.roles.customBadge")}</span>
                  <span className="role-id">{role.id}</span>
                  <p>{role.description}</p>
                </div>
                <div className="role-card-actions">
                  <button type="button" onClick={() => startEdit(role)} disabled={busy}>
                    {t("settings.roles.edit")}
                  </button>
                  <button
                    type="button"
                    className="btn-deny"
                    onClick={() => void handleDelete(role)}
                    disabled={busy}
                  >
                    {t("settings.roles.delete")}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
