import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { SystemStatus } from "./SystemStatus";

test("system actions and token editing are disabled while another action is busy", () => {
  const onApiTokenChange = vi.fn();
  render(
    <SystemStatus
      system={null}
      loading={false}
      apiToken="token"
      resetBusy={true}
      onApiTokenChange={onApiTokenChange}
      onRefresh={vi.fn()}
      onReset={vi.fn()}
    />
  );

  expect(screen.getByLabelText("Local API token")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Refresh system status" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reset history & cache" })).toBeDisabled();

  fireEvent.submit(screen.getByRole("button", { name: "Apply" }).closest("form")!);
  expect(onApiTokenChange).not.toHaveBeenCalled();
});
