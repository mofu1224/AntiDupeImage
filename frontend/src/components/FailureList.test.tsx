import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { formatLocalTimestamp } from "../utils/dateTime";
import { FailureList } from "./FailureList";

test("expands full failure details and pages forward", () => {
  const onPage = vi.fn();
  render(
    <FailureList
      failures={[
        {
          id: 1,
          path: "/tmp/images/problem.png",
          stage: "features",
          error: "Unable to decode image because the file changed during scanning",
          created_at: "2026-06-12 00:00:00"
        }
      ]}
      total={101}
      limit={100}
      offset={0}
      loading={false}
      error={null}
      onRetry={vi.fn()}
      onPage={onPage}
    />
  );

  const details = screen.getByText("/tmp/images/problem.png").closest("details");
  expect(details).not.toHaveAttribute("open");
  fireEvent.click(screen.getByText("/tmp/images/problem.png"));
  expect(details).toHaveAttribute("open");
  expect(screen.getAllByText("Unable to decode image because the file changed during scanning"))
    .toHaveLength(2);
  expect(screen.getByText(formatLocalTimestamp("2026-06-12 00:00:00"))).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next failure page" }));

  expect(onPage).toHaveBeenCalledWith(100);
});

test("copies the current failure page and can reuse scan settings", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText }
  });
  const onReuseSettings = vi.fn();
  render(
    <FailureList
      failures={[
        {
          id: 2,
          path: "C:\\photos\\broken.png",
          stage: "metadata",
          error: "Permission denied\nThe file is currently locked",
          created_at: "2026-06-12T01:02:03Z"
        }
      ]}
      total={1}
      limit={100}
      offset={0}
      loading={false}
      error={null}
      onRetry={vi.fn()}
      onPage={vi.fn()}
      onReuseSettings={onReuseSettings}
    />
  );

  fireEvent.click(screen.getByRole("button", { name: "Copy this page" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(
    "[metadata] C:\\photos\\broken.png\nPermission denied\nThe file is currently locked\n2026-06-12T01:02:03Z"
  ));
  expect(screen.getByRole("status")).toHaveTextContent("Copied 1 issue(s) from this page.");

  fireEvent.click(screen.getByRole("button", { name: "Use scan settings" }));
  expect(onReuseSettings).toHaveBeenCalledTimes(1);
});
