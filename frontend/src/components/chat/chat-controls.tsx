"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, Cpu, Settings2, Sliders, Plug, Telescope } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  FormField,
  Input,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { useConversationStore } from "@/stores";
import { useChatModeStore } from "@/stores";
import { cn } from "@/lib/utils";
import Link from "next/link";
import { toast } from "sonner";
import { useMcpConnections, useWorkspaces } from "@/hooks";
import { DEFAULT_THINKING_EFFORT, ROUTES } from "@/lib/constants";
import type { McpConnectionRecord } from "@/lib/mcp-connections-api";

type ThinkingEffort = "off" | "low" | "medium" | "high";
type Tab = "model" | "settings" | "plugins";

interface ChatControlsProps {
  onModelChange?: (model: string | null) => void;
  onTemperatureChange?: (value: number | null) => void;
  onThinkingEffortChange?: (value: "low" | "medium" | "high" | null) => void;
}

const EFFORT_OPTIONS: { label: string; value: ThinkingEffort; hint: string }[] = [
  { label: "Off", value: "off", hint: "Direct answer, no reasoning" },
  { label: "Low", value: "low", hint: "Quick reasoning" },
  { label: "Medium", value: "medium", hint: "Balanced" },
  { label: "High", value: "high", hint: "Deep, slower" },
];

/**
 * Unified popover panel that replaces the 3 separate triggers (KB / Model /
 * Chat settings) with a single button that summarizes current state and opens
 * a tabbed control surface.
 */
export function ChatControls({
  onModelChange,
  onTemperatureChange,
  onThinkingEffortChange,
}: ChatControlsProps) {
  const [tab, setTab] = useState<Tab>("model");
  const { currentConversationId } = useConversationStore();

  const [availableModels, setAvailableModels] = useState<{ value: string; label: string }[]>([
    { value: "", label: "Default" },
  ]);
  const [selectedModel, setSelectedModel] = useState<{ value: string; label: string }>({
    value: "",
    label: "Default",
  });

  useEffect(() => {
    // Fetch model list once on mount. `onModelChange` is intentionally NOT in
    // deps — parents (use-chat) pass an inline arrow each render, so depending
    // on it triggers a refetch every render → infinite loop during streaming.
    fetch("/api/v1/agent/models", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.models) {
          const models = [
            { value: "", label: `Default (${data.default})` },
            ...data.models.map((m: string) => ({ value: m, label: m })),
          ];
          setAvailableModels(models);
          setSelectedModel(models[0]);
        }
      })
      .catch(() => {});
  }, []);

  // The latest model name the user typed in rather than picked from the server
  // list — persisted in the chat-mode store so it stays available across chats.
  const customModel = useChatModeStore((s) => s.customModel);
  const setCustomModel = useChatModeStore((s) => s.setCustomModel);

  const modelOptions =
    customModel && !availableModels.some((m) => m.value === customModel)
      ? [...availableModels, { value: customModel, label: customModel }]
      : availableModels;

  const pickModel = (m: { value: string; label: string }) => {
    setSelectedModel(m);
    onModelChange?.(m.value || null);
  };

  const applyCustomModel = (name: string) => {
    const value = name.trim();
    if (!value) return;
    if (value !== customModel) setCustomModel(value);
    pickModel({ value, label: value });
  };

  const [temperature, setTemperature] = useState<number | null>(null);
  const [effort, setEffort] = useState<ThinkingEffort>(DEFAULT_THINKING_EFFORT);
  const settingsOverridden = temperature !== null || effort !== DEFAULT_THINKING_EFFORT;
  const deepResearch = useChatModeStore((s) => s.deepResearch);

  const triggerSummary = useMemo(() => {
    const parts: string[] = [];
    if (deepResearch) parts.push("Research");
    if (selectedModel.value) parts.push(selectedModel.value);
    if (settingsOverridden) parts.push("Custom");
    return parts.length ? parts.join(" · ") : "Controls";
  }, [deepResearch, selectedModel, settingsOverridden]);

  const hasOverrides = deepResearch || selectedModel.value !== "" || settingsOverridden;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Chat controls"
          className={cn(
            "border-foreground/10 bg-card hover:border-foreground/25 hover:bg-foreground/[0.04] inline-flex items-center gap-1.5 rounded-full border py-1 pr-2 pl-2.5 font-mono text-[11px] tracking-wider uppercase transition-colors",
            hasOverrides ? "text-foreground" : "text-foreground/65",
          )}
        >
          <Sliders className="h-3 w-3" />
          <span className="max-w-[200px] truncate">{triggerSummary}</span>
          {hasOverrides && (
            <span aria-hidden className="bg-foreground inline-block h-1 w-1 rounded-full" />
          )}
          <ChevronDown className="text-foreground/45 h-3 w-3" />
        </button>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        sideOffset={8}
        className="border-border bg-popover relative w-[380px] overflow-hidden rounded-2xl border p-0 shadow-md"
      >
        <div className="border-foreground/10 flex items-center gap-1 border-b p-2">
          {onModelChange && (
            <TabButton
              icon={Cpu}
              label="Model"
              active={tab === "model"}
              onClick={() => setTab("model")}
            />
          )}
          {onTemperatureChange && onThinkingEffortChange && (
            <TabButton
              icon={Settings2}
              label="Settings"
              active={tab === "settings"}
              onClick={() => setTab("settings")}
            />
          )}
          <TabButton
            icon={Plug}
            label="Plugins"
            active={tab === "plugins"}
            onClick={() => setTab("plugins")}
          />
        </div>

        <div className="max-h-[420px] scrollbar-thin overflow-y-auto p-4">
          {tab === "model" && (
            <ModelPanel
              models={modelOptions}
              selected={selectedModel}
              onPick={pickModel}
              onCustomSubmit={applyCustomModel}
            />
          )}
          {tab === "settings" && (
            <SettingsPanel
              temperature={temperature}
              effort={effort}
              onTemperatureChange={(v) => {
                setTemperature(v);
                onTemperatureChange?.(v);
              }}
              onEffortChange={(v) => {
                setEffort(v);
                onThinkingEffortChange?.(v === "off" ? null : v);
              }}
            />
          )}
          {tab === "plugins" && <PluginsPanel />}
        </div>

        <div className="border-foreground/10 text-foreground/45 flex items-center justify-between border-t px-4 py-2 font-mono text-[10px] tracking-wider uppercase">
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="bg-foreground inline-block h-1 w-1 animate-pulse rounded-full"
            />
            {currentConversationId ? "Saved for this chat" : "Saves on send"}
          </span>
          <span>esc to close</span>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function TabButton({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex flex-1 items-center justify-center gap-1.5 rounded-full px-3 py-1.5 font-mono text-[11px] tracking-wider uppercase transition-colors",
        active
          ? "bg-foreground text-background"
          : "text-foreground/55 hover:bg-foreground/[0.04] hover:text-foreground",
      )}
    >
      <Icon className="h-3 w-3" />
      {label}
    </button>
  );
}

/** Model picker panel. */
function ModelPanel({
  models,
  selected,
  onPick,
  onCustomSubmit,
}: {
  models: { value: string; label: string }[];
  selected: { value: string; label: string };
  onPick: (m: { value: string; label: string }) => void;
  onCustomSubmit: (name: string) => void;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div>
      <p className="text-foreground mb-1 text-sm font-semibold">Model</p>
      <p className="text-foreground/55 mb-4 text-xs leading-relaxed">
        Pick the model that handles this conversation.
      </p>
      <ul className="space-y-1">
        {models.map((m) => {
          const isActive = selected.value === m.value;
          return (
            <li key={m.value || "default"}>
              <button
                type="button"
                onClick={() => onPick(m)}
                className={cn(
                  "flex w-full items-center justify-between rounded-xl border px-3 py-2.5 text-left text-xs transition-all",
                  isActive
                    ? "border-foreground/30 bg-accent text-foreground"
                    : "border-border text-foreground/75 hover:border-foreground/25 hover:bg-accent/60 hover:text-foreground",
                )}
              >
                <span className="truncate font-medium">{m.label}</span>
                {isActive && <Check className="text-foreground h-3.5 w-3.5 shrink-0" />}
              </button>
            </li>
          );
        })}
      </ul>
      <FormField
        htmlFor="chat-custom-model"
        label="Custom model"
        description="Press Enter to use a model that isn't listed. Your latest custom model is kept in this list for future chats."
        className="mt-4"
      >
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onCustomSubmit(draft);
              setDraft("");
            }
          }}
          placeholder="provider/model-name"
          spellCheck={false}
          autoComplete="off"
        />
      </FormField>
    </div>
  );
}
/** Plugins panel — toggle the user's MCP servers on/off for the assistant. */
function PluginsPanel() {
  const { connections, isLoading, update } = useMcpConnections();

  const handleToggle = async (connection: McpConnectionRecord, next: boolean) => {
    try {
      await update(connection.id, { is_enabled: next });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to toggle plugin");
    }
  };

  return (
    <div>
      <p className="text-foreground mb-1 text-sm font-semibold">Plugins</p>
      <p className="text-foreground/55 mb-4 text-xs leading-relaxed">
        MCP servers your assistant can pull tools from. Changes apply from your next message.
      </p>

      {connections.length === 0 ? (
        <p className="text-foreground/55 text-xs leading-relaxed">
          No servers connected yet.{" "}
          <Link
            href={ROUTES.SETTINGS_INTEGRATIONS}
            className="text-foreground underline underline-offset-2"
          >
            Add one in Settings → Integrations
          </Link>
          .
        </p>
      ) : (
        <>
          <ul className="space-y-1">
            {connections.map((connection) => (
              <li
                key={connection.id}
                className="border-border flex items-center gap-3 rounded-xl border px-3 py-2.5"
              >
                <span
                  aria-hidden
                  className={cn(
                    "h-1.5 w-1.5 shrink-0 rounded-full",
                    // Off (toggle disabled) reads as muted/inactive, regardless of last test.
                    !connection.is_enabled && "bg-foreground/20",
                    connection.is_enabled && connection.last_status === "ok" && "bg-emerald-500",
                    connection.is_enabled && connection.last_status === "error" && "bg-destructive",
                    connection.is_enabled && !connection.last_status && "bg-foreground/40",
                  )}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-foreground truncate text-xs font-medium">{connection.name}</p>
                  <p className="text-foreground/45 text-[10px]">
                    {connection.allowed_tools === null
                      ? "all tools"
                      : `${connection.allowed_tools.length} tools`}
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={connection.is_enabled}
                  disabled={isLoading}
                  onClick={() => handleToggle(connection, !connection.is_enabled)}
                  className={cn(
                    "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
                    connection.is_enabled ? "bg-primary" : "bg-foreground/20",
                  )}
                >
                  <span
                    className={cn(
                      "bg-background inline-block h-4 w-4 transform rounded-full shadow transition-transform",
                      connection.is_enabled ? "translate-x-4" : "translate-x-0.5",
                    )}
                  />
                </button>
              </li>
            ))}
          </ul>
          <Link
            href={ROUTES.SETTINGS_INTEGRATIONS}
            className="text-foreground/55 hover:text-foreground mt-3 inline-block text-[11px] underline-offset-2 hover:underline"
          >
            Manage servers & tools in Settings
          </Link>
        </>
      )}
    </div>
  );
}

/** Coding workspace for the next turn (ADR-006).
 *
 * Selecting one attaches sandboxed file and shell tools to that turn. The
 * workspace's ruleset decides what is registered and what asks for approval;
 * an auto-approving workspace is called out here because the override lasts
 * for the whole conversation. */
const NO_WORKSPACE = "__none__";

function WorkspacePicker() {
  const { workspaces, isLoading } = useWorkspaces();
  const workspaceId = useChatModeStore((s) => s.workspaceId);
  const setWorkspaceId = useChatModeStore((s) => s.setWorkspaceId);
  const selected = workspaces.find((w) => w.id === workspaceId) ?? null;

  // Never leave a turn pointing at a workspace that no longer exists.
  useEffect(() => {
    if (workspaceId && !isLoading && !selected) setWorkspaceId(null);
  }, [workspaceId, isLoading, selected, setWorkspaceId]);

  if (!isLoading && workspaces.length === 0) return null;

  return (
    <div className="space-y-2.5">
      <label htmlFor="chat-workspace" className="text-foreground text-sm font-semibold">
        Coding workspace
      </label>
      <Select
        value={workspaceId ?? NO_WORKSPACE}
        onValueChange={(v) => setWorkspaceId(v === NO_WORKSPACE ? null : v)}
      >
        <SelectTrigger id="chat-workspace">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_WORKSPACE}>None — chat only</SelectItem>
          {workspaces.map((w) => (
            <SelectItem key={w.id} value={w.id}>
              {w.name} ({w.ruleset})
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-foreground/55 text-[11px] leading-relaxed">
        {selected === null
          ? "The assistant has no file or shell tools this turn."
          : selected.auto_approve
            ? `Writes and commands in ${selected.name} run without asking — for this whole conversation.`
            : selected.ruleset === "readonly"
              ? `${selected.name} is read-only: the assistant can browse and search, not change anything.`
              : `Every write and command in ${selected.name} asks for your approval first.`}
      </p>
    </div>
  );
}

/** Chat settings panel — temperature + thinking effort. */
function SettingsPanel({
  temperature,
  effort,
  onTemperatureChange,
  onEffortChange,
}: {
  temperature: number | null;
  effort: ThinkingEffort;
  onTemperatureChange: (v: number | null) => void;
  onEffortChange: (v: ThinkingEffort) => void;
}) {
  const deepResearch = useChatModeStore((s) => s.deepResearch);
  const setDeepResearch = useChatModeStore((s) => s.setDeepResearch);
  return (
    <div className="space-y-6">
      <div className="space-y-2.5">
        <div className="flex items-center justify-between gap-3">
          <span className="text-foreground inline-flex items-center gap-1.5 text-sm font-semibold">
            <Telescope className="h-3.5 w-3.5" />
            Deep research
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={deepResearch}
            onClick={() => setDeepResearch(!deepResearch)}
            className={cn(
              "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
              deepResearch ? "bg-primary" : "bg-foreground/20",
            )}
          >
            <span
              className={cn(
                "bg-background inline-block h-4 w-4 transform rounded-full shadow transition-transform",
                deepResearch ? "translate-x-4" : "translate-x-0.5",
              )}
            />
          </button>
        </div>
        <p className="text-foreground/55 text-[11px] leading-relaxed">
          {deepResearch
            ? "Plans the work, delegates to parallel subagents, then composes a cited report — asking you to clarify the scope first when the request is vague."
            : "Answers directly in a single fast pass, with no planning or delegation."}
        </p>
      </div>
      <WorkspacePicker />
      <div className="space-y-2.5">
        <div className="flex items-baseline justify-between">
          <label htmlFor="chat-temp" className="text-foreground text-sm font-semibold">
            Temperature
          </label>
          <span className="text-foreground font-mono text-xs tabular-nums">
            {temperature === null ? (
              <span className="text-foreground/55">default</span>
            ) : (
              temperature.toFixed(2)
            )}
          </span>
        </div>
        <input
          id="chat-temp"
          type="range"
          min={0}
          max={2}
          step={0.05}
          value={temperature ?? 0.7}
          onChange={(e) => onTemperatureChange(parseFloat(e.target.value))}
          className="bg-foreground/15 h-1.5 w-full cursor-pointer appearance-none rounded-full accent-[var(--color-brand)]"
        />
        <div className="text-foreground/45 flex justify-between font-mono text-[10px] tracking-wider uppercase">
          <span>focused</span>
          <span>creative</span>
        </div>
        {temperature !== null && (
          <button
            type="button"
            onClick={() => onTemperatureChange(null)}
            className="text-foreground/55 hover:text-foreground text-[11px] underline-offset-2 hover:underline"
          >
            Reset to server default
          </button>
        )}
      </div>

      <div className="space-y-2.5">
        <div className="flex items-baseline justify-between">
          <span className="text-foreground text-sm font-semibold">Thinking effort</span>
          <span className="text-foreground/45 text-[10px]">model-dependent</span>
        </div>
        <div className="grid grid-cols-4 gap-1">
          {EFFORT_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => onEffortChange(opt.value)}
              className={cn(
                "rounded-lg px-2 py-1.5 font-mono text-[11px] tracking-wider uppercase transition-colors",
                effort === opt.value
                  ? "bg-foreground text-background"
                  : "border-foreground/15 text-foreground/55 hover:text-foreground border",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <p className="text-foreground/55 text-[11px]">
          {EFFORT_OPTIONS.find((o) => o.value === effort)?.hint}
        </p>
      </div>

      <p className="text-foreground/45 text-[10px] leading-relaxed">
        Settings persist for the current chat session. Some controls are no-ops on models that
        don&apos;t support them.
      </p>
    </div>
  );
}
