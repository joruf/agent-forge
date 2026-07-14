import type { ContextPluginInfo } from "../types";
import { useI18n } from "../hooks/useI18n";

interface ContextPluginsListProps {
  plugins: ContextPluginInfo[];
  enabled: string[];
}

export function ContextPluginsList({ plugins, enabled }: ContextPluginsListProps) {
  const { t } = useI18n();

  if (plugins.length === 0) {
    return <p className="settings-context-plugins-empty">{t("settings.contextPluginsEmpty")}</p>;
  }

  return (
    <ul className="settings-context-plugins-list">
      {plugins.map((plugin) => {
        const isEnabled = enabled.includes(plugin.id);
        return (
          <li key={plugin.id} className="settings-context-plugins-item">
            <div className="settings-context-plugins-item-head">
              <strong>{plugin.name}</strong>
              <span className={`settings-context-plugins-status ${isEnabled ? "enabled" : "disabled"}`}>
                {isEnabled ? t("settings.contextPluginEnabled") : t("settings.contextPluginDisabled")}
              </span>
            </div>
            <p>{plugin.description}</p>
            <small>
              {plugin.api_name} · {plugin.api_key_required ? t("context.apiKeyRequired") : t("context.noApiKey")}
            </small>
            {plugin.trigger_keywords.length > 0 && (
              <div className="settings-context-plugins-keywords">
                {plugin.trigger_keywords.slice(0, 8).join(", ")}
                {plugin.trigger_keywords.length > 8 ? "…" : ""}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
