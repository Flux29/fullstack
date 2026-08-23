"use client";

import { SectionCard } from "@/components/settings/settings-section";
import { WorkspacesManager } from "@/components/settings/workspaces-manager";

export default function WorkspacesSettingsPage() {
  return (
    <div className="space-y-6">
      <SectionCard
        title="Workspaces"
        description="Sandboxed repositories the assistant can code in. Each workspace carries its own ruleset and approval policy."
      >
        <WorkspacesManager />
      </SectionCard>
    </div>
  );
}
