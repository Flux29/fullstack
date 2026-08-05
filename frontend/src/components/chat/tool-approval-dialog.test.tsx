import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ToolApprovalDialog } from "./tool-approval-dialog";

const actions = [{ id: "call-1", tool_name: "drive_delete_file", args: { file_id: "one" } }];

describe("ToolApprovalDialog", () => {
  it("returns call IDs for approval and edited arguments", () => {
    const onDecisions = vi.fn();
    render(
      <ToolApprovalDialog actionRequests={actions} reviewConfigs={[]} onDecisions={onDecisions} />,
    );

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: JSON.stringify({ file_id: "two" }) },
    });
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));

    expect(onDecisions).toHaveBeenCalledWith([
      {
        id: "call-1",
        tool_name: "drive_delete_file",
        type: "edit",
        args: { file_id: "two" },
      },
    ]);
  });

  it("can explicitly reject every pending mutation", () => {
    const onDecisions = vi.fn();
    render(
      <ToolApprovalDialog actionRequests={actions} reviewConfigs={[]} onDecisions={onDecisions} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Reject all" }));

    expect(onDecisions).toHaveBeenCalledWith([
      { id: "call-1", tool_name: "drive_delete_file", type: "reject" },
    ]);
  });
});
