"use client";

import { useState } from "react";
import { FolderGit2, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  IconButton,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
} from "@/components/ui";
import { EmptyState } from "@/components/states";
import { useWorkspaces } from "@/hooks";
import { getErrorMessage } from "@/lib/utils";
import type { WorkspaceBackendKind, WorkspaceRecord, WorkspaceRuleset } from "@/lib/workspaces-api";

const NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,31}$/;

const RULESETS: { value: WorkspaceRuleset; label: string; hint: string }[] = [
  { value: "readonly", label: "Read-only", hint: "Browse and search only — no write or shell tools." },
  { value: "default", label: "Default", hint: "Every write and command asks for approval." },
  { value: "strict", label: "Strict", hint: "Every tool, including reads, asks for approval." },
];

const BACKENDS: { value: WorkspaceBackendKind; label: string }[] = [
  { value: "remote", label: "Sandbox service" },
  { value: "docker", label: "Local Docker (host backend only)" },
];

interface Draft {
  name: string;
  backend_kind: WorkspaceBackendKind;
  repo_url: string;
  ruleset: WorkspaceRuleset;
  auto_approve: boolean;
}

/** Auto-approve is meaningless on a read-only workspace (no write or execute
 * tool is registered, so there is nothing to approve) and contradictory on a
 * strict one. The backend clears the first and refuses the second; the control
 * tells the same story. */
function canAutoApprove(ruleset: WorkspaceRuleset): boolean {
  return ruleset === "default";
}

const EMPTY_DRAFT: Draft = {
  name: "",
  backend_kind: "remote",
  repo_url: "",
  ruleset: "default",
  auto_approve: false,
};

export function WorkspacesManager() {
  const { workspaces, isLoading, error, refresh, create, update, remove } = useWorkspaces();

  const [editingId, setEditingId] = useState<string | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [submitting, setSubmitting] = useState(false);

  const patchDraft = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));

  const openCreate = () => {
    setEditingId("new");
    setDraft(EMPTY_DRAFT);
  };

  const openEdit = (w: WorkspaceRecord) => {
    setEditingId(w.id);
    setDraft({
      name: w.name,
      backend_kind: w.backend_kind,
      repo_url: w.repo_url ?? "",
      ruleset: w.ruleset,
      auto_approve: w.auto_approve,
    });
  };

  const closeDialog = () => {
    if (submitting) return;
    setEditingId(null);
  };

  const strictConflict = draft.ruleset === "strict" && draft.auto_approve;

  const handleSubmit = async () => {
    const name = draft.name.trim().toLowerCase();
    if (!NAME_PATTERN.test(name)) {
      toast.error("Name must be lowercase letters, digits, and hyphens (max 32 chars).");
      return;
    }
    const repo_url = draft.repo_url.trim();
    setSubmitting(true);
    try {
      if (editingId === "new") {
        await create({
          name,
          backend_kind: draft.backend_kind,
          repo_url: repo_url || null,
          ruleset: draft.ruleset,
          auto_approve: draft.auto_approve,
        });
        toast.success(`Workspace ${name} created.`);
      } else if (editingId) {
        await update(editingId, {
          name,
          backend_kind: draft.backend_kind,
          repo_url: repo_url || null,
          ruleset: draft.ruleset,
          auto_approve: draft.auto_approve,
        });
        toast.success(`Workspace ${name} updated.`);
      }
      setEditingId(null);
    } catch (e) {
      toast.error(getErrorMessage(e, "Failed to save workspace"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleToggleAutoApprove = async (w: WorkspaceRecord, next: boolean) => {
    try {
      await update(w.id, { auto_approve: next });
    } catch (e) {
      toast.error(getErrorMessage(e, "Failed to toggle auto-approve"));
    }
  };

  const handleDelete = async (w: WorkspaceRecord) => {
    if (!confirm(`Delete workspace ${w.name}? The sandbox contents are reaped separately.`)) return;
    try {
      await remove(w.id);
      toast.success(`Workspace ${w.name} deleted.`);
    } catch (e) {
      toast.error(getErrorMessage(e, "Failed to delete"));
    }
  };

  return (
    <div className="space-y-6">
      {error && (
        <div className="border-destructive/30 bg-destructive/5 text-destructive flex items-center justify-between rounded-xl border px-4 py-3 text-sm">
          <span>{error}</span>
          <Button size="sm" variant="ghost" onClick={() => refresh()}>
            Retry
          </Button>
        </div>
      )}

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <div>
            <h3 className="text-foreground text-sm font-semibold">Your workspaces</h3>
            <p className="text-foreground/55 mt-0.5 text-xs">
              A sandboxed checkout the assistant can read, edit, and run commands in. Pick one
              per chat turn from the chat controls.
            </p>
          </div>
          <Button size="sm" onClick={openCreate}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            New workspace
          </Button>
        </div>

        {!isLoading && workspaces.length === 0 ? (
          <EmptyState
            icon={FolderGit2}
            title="No workspaces yet"
            description="Create one to let the assistant work on a repository."
          />
        ) : (
          <ul className="border-foreground/10 divide-foreground/8 divide-y rounded-xl border">
            {workspaces.map((w) => (
              <li key={w.id} className="flex items-start gap-4 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <code className="text-foreground bg-foreground/8 rounded px-1.5 py-0.5 font-mono text-xs">
                      {w.name}
                    </code>
                    <Badge variant="outline">{w.ruleset}</Badge>
                    {w.auto_approve && <Badge variant="secondary">auto-approve</Badge>}
                    <span className="text-foreground/45 font-mono text-[10px] tracking-wider uppercase">
                      {w.backend_kind}
                    </span>
                  </div>
                  <p className="text-foreground/65 mt-1 truncate text-xs">
                    {w.repo_url ?? "No repository attached"}
                  </p>
                </div>
                <Switch
                  checked={w.auto_approve}
                  disabled={!canAutoApprove(w.ruleset)}
                  onCheckedChange={(v) => handleToggleAutoApprove(w, v)}
                  aria-label={`Toggle auto-approve for ${w.name}`}
                />
                <IconButton onClick={() => openEdit(w)} title="Edit" aria-label="Edit">
                  <Pencil className="h-3.5 w-3.5" />
                </IconButton>
                <IconButton onClick={() => handleDelete(w)} title="Delete" aria-label="Delete">
                  <Trash2 className="h-3.5 w-3.5" />
                </IconButton>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Dialog open={editingId !== null} onOpenChange={(o) => !o && closeDialog()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingId === "new" ? "New workspace" : `Edit ${draft.name}`}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label htmlFor="ws-name">Name</Label>
              <Input
                id="ws-name"
                value={draft.name}
                onChange={(e) => patchDraft({ name: e.target.value.toLowerCase() })}
                placeholder="my-app"
                maxLength={32}
                autoFocus
              />
              <p className="text-foreground/45 mt-1 text-[11px]">
                Lowercase letters, digits, hyphens. Max 32 chars.
              </p>
            </div>
            <div>
              <Label htmlFor="ws-repo">Repository URL</Label>
              <Input
                id="ws-repo"
                value={draft.repo_url}
                onChange={(e) => patchDraft({ repo_url: e.target.value })}
                placeholder="https://github.com/org/repo"
                maxLength={2048}
                className="mt-1.5 font-mono text-sm"
              />
              <p className="text-foreground/45 mt-1 text-[11px]">
                https only. Tokens in the URL are stripped — private repositories are not yet
                supported.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="ws-ruleset">Ruleset</Label>
                <Select
                  value={draft.ruleset}
                  onValueChange={(v) => patchDraft({ ruleset: v as WorkspaceRuleset })}
                >
                  <SelectTrigger id="ws-ruleset" className="mt-1.5">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RULESETS.map((r) => (
                      <SelectItem key={r.value} value={r.value}>
                        {r.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-foreground/45 mt-1 text-[11px]">
                  {RULESETS.find((r) => r.value === draft.ruleset)?.hint}
                </p>
              </div>
              <div>
                <Label htmlFor="ws-backend">Sandbox</Label>
                <Select
                  value={draft.backend_kind}
                  onValueChange={(v) => patchDraft({ backend_kind: v as WorkspaceBackendKind })}
                >
                  <SelectTrigger id="ws-backend" className="mt-1.5">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {BACKENDS.map((b) => (
                      <SelectItem key={b.value} value={b.value}>
                        {b.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <Switch
                id="ws-auto"
                checked={draft.auto_approve}
                disabled={draft.ruleset === "readonly"}
                onCheckedChange={(v) => patchDraft({ auto_approve: v })}
              />
              <Label htmlFor="ws-auto" className="text-sm font-normal">
                Auto-approve writes and commands in this workspace
              </Label>
            </div>
            {strictConflict && (
              <p className="text-destructive text-[11px]">
                Auto-approve cannot be combined with the strict ruleset.
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={closeDialog} disabled={submitting}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={submitting || strictConflict}>
              {submitting ? "Saving…" : editingId === "new" ? "Create" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
