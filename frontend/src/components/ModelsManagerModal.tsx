import { useEffect, useState } from "react";
import { ALL_TASKS } from "../constants/tasks";
import { api } from "../services/api";
import { useI18n } from "../hooks/useI18n";
import { useEscapeClose } from "../hooks/useEscapeClose";
import type { LLMRoutingInfo, ModelSuggestion, UserModel } from "../types";

interface ModelsManagerModalProps {
  open: boolean;
  routing: LLMRoutingInfo | null;
  onClose: () => void;
  onUpdated: (routing: LLMRoutingInfo) => void;
}

export function ModelsManagerModal({
  open,
  routing,
  onClose,
  onUpdated,
}: ModelsManagerModalProps) {
  const { t } = useI18n();
  const [models, setModels] = useState<UserModel[]>([]);
  const [routingMap, setRoutingMap] = useState<Record<string, string>>({});
  const [newTag, setNewTag] = useState("");
  const [suggestion, setSuggestion] = useState<ModelSuggestion | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const taskLabel = (task: string) => t(`tasks.${task}`, {}) === `tasks.${task}` ? task : t(`tasks.${task}`);

  useEffect(() => {
    if (open && routing) {
      setModels(routing.models);
      setRoutingMap(routing.routing ?? {});
    }
  }, [open, routing]);

  useEscapeClose(open, onClose);

  if (!open) {
    return null;
  }

  const refresh = async () => {
    const data = await api.getLLMRouting();
    setModels(data.models);
    setRoutingMap(data.routing ?? {});
    onUpdated(data);
  };

  const handleSuggest = async () => {
    if (!newTag.trim()) return;
    setError("");
    try {
      const result = await api.suggestModel(newTag.trim());
      setSuggestion(result);
    } catch (err) {
      setError(String(err));
    }
  };

  const handleAdd = async () => {
    if (!newTag.trim()) return;
    setLoading(true);
    setError("");
    try {
      await api.createUserModel({
        ollama_tag: newTag.trim(),
        display_name: suggestion?.display_name,
        assigned_tasks: suggestion?.assigned_tasks,
        notes: suggestion?.description,
        auto_suggest: !suggestion,
      });
      setNewTag("");
      setSuggestion(null);
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async () => {
    setLoading(true);
    setError("");
    try {
      await api.syncOllamaModels();
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    await api.deleteUserModel(id);
    await refresh();
  };

  const handleToggleTask = async (model: UserModel, task: string) => {
    const tasks = model.assigned_tasks.includes(task)
      ? model.assigned_tasks.filter((item) => item !== task)
      : [...model.assigned_tasks, task];
    await api.updateUserModel(model.id, { assigned_tasks: tasks });
    await refresh();
  };

  const handleRoutingChange = async (task: string, modelId: string) => {
    const next = { ...routingMap, [task]: modelId };
    setRoutingMap(next);
    const result = await api.updateRouting({ [task]: modelId });
    setRoutingMap(result.routing);
    onUpdated({
      ...(routing as LLMRoutingInfo),
      routing: result.routing,
      tasks: result.tasks,
    });
  };

  const handleSaveEdit = async (model: UserModel, displayName: string, notes: string) => {
    await api.updateUserModel(model.id, { display_name: displayName, notes });
    setEditingId(null);
    await refresh();
  };

  const enabledModels = models.filter((model) => model.enabled);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal models-modal" onClick={(event) => event.stopPropagation()}>
        <h2>{t("models.title")}</h2>
        <div className="modal-body">
          <p className="models-lead">{t("models.lead")}</p>

          {error && <p className="routing-warning">{error}</p>}

        <section className="models-section">
          <h3>{t("models.addSection")}</h3>
          <div className="models-add-row">
            <input
              value={newTag}
              onChange={(event) => setNewTag(event.target.value)}
              placeholder={t("models.tagPlaceholder")}
            />
            <button type="button" onClick={() => void handleSuggest()}>
              {t("models.suggest")}
            </button>
            <button type="button" className="btn-primary" disabled={loading} onClick={() => void handleAdd()}>
              {t("models.add")}
            </button>
            <button type="button" disabled={loading} onClick={() => void handleSync()}>
              {t("models.importOllama")}
            </button>
          </div>
          {suggestion && (
            <div className="model-suggestion">
              <strong>{suggestion.display_name}</strong>
              <p>{suggestion.description}</p>
              <p>
                {t("models.recommendedFor")}{" "}
                {suggestion.assigned_tasks.map((task) => taskLabel(task)).join(", ")}
              </p>
              {suggestion.ram_gb && <p>{t("models.ram", { gb: suggestion.ram_gb })}</p>}
            </div>
          )}
        </section>

        <section className="models-section">
          <h3>{t("models.availableModels", { count: models.length })}</h3>
          <div className="models-list">
            {models.length === 0 && (
              <p className="routing-hint">{t("models.noModels")}</p>
            )}
            {models.map((model) => (
              <div key={model.id} className={`model-card ${model.enabled ? "" : "disabled"}`}>
                <div className="model-card-header">
                  <div>
                    <strong>{model.display_name}</strong>
                    <span className="model-tag">{model.ollama_tag}</span>
                  </div>
                  <div className="model-card-actions">
                    <button type="button" onClick={() => setEditingId(editingId === model.id ? null : model.id)}>
                      {t("models.edit")}
                    </button>
                    <button type="button" className="btn-deny" onClick={() => void handleDelete(model.id)}>
                      {t("models.delete")}
                    </button>
                  </div>
                </div>
                {editingId === model.id ? (
                  <ModelEditForm model={model} onSave={handleSaveEdit} />
                ) : (
                  <>
                    {model.notes && <p className="model-notes">{model.notes}</p>}
                    <div className="task-chips">
                      {ALL_TASKS.map((task) => (
                        <label key={task} className="task-chip">
                          <input
                            type="checkbox"
                            checked={model.assigned_tasks.includes(task)}
                            onChange={() => void handleToggleTask(model, task)}
                          />
                          {taskLabel(task)}
                        </label>
                      ))}
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </section>

        <section className="models-section">
          <h3>{t("models.routingSection")}</h3>
          <div className="routing-table">
            {Object.entries(routing?.tasks ?? {}).map(([task, info]) => (
              <label key={task} className="routing-row">
                <span className="routing-label">
                  <strong>{info.label || taskLabel(task)}</strong>
                  <small>{info.description}</small>
                </span>
                <select
                  value={routingMap[task] ?? "auto"}
                  onChange={(event) => void handleRoutingChange(task, event.target.value)}
                >
                  <option value="auto">
                    {t("models.autoOption", { model: info.selected.replace(/^ollama\//, "") })}
                  </option>
                  {enabledModels.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.display_name} ({model.ollama_tag})
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        </section>
        </div>

        <div className="modal-actions">
          <button type="button" className="btn-primary" onClick={onClose}>
            {t("models.close")}
          </button>
        </div>
      </div>
    </div>
  );
}

interface ModelEditFormProps {
  model: UserModel;
  onSave: (model: UserModel, displayName: string, notes: string) => Promise<void>;
}

function ModelEditForm({ model, onSave }: ModelEditFormProps) {
  const { t } = useI18n();
  const [displayName, setDisplayName] = useState(model.display_name);
  const [notes, setNotes] = useState(model.notes);

  return (
    <div className="model-edit-form">
      <label>
        {t("models.displayName")}
        <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
      </label>
      <label>
        {t("models.notes")}
        <input value={notes} onChange={(event) => setNotes(event.target.value)} />
      </label>
      <button type="button" className="btn-primary" onClick={() => void onSave(model, displayName, notes)}>
        {t("common.save")}
      </button>
    </div>
  );
}
