import { fireEvent, render, screen } from "@testing-library/react";
import { waitFor } from "@testing-library/react";
import { vi } from "vitest";
import type { SystemResponse } from "../types/api";
import { ScanForm } from "./ScanForm";

const mocks = vi.hoisted(() => ({
  chooseFolder: vi.fn()
}));

vi.mock("../api/client", () => ({
  chooseFolder: mocks.chooseFolder
}));

const clipSystem: SystemResponse = {
  python: "3.12",
  platform: "test",
  app_dir: "/tmp/app",
  db_path: "/tmp/app/db.sqlite3",
  device: {
    selected: "cpu",
    available: ["cpu", "cuda:0"],
    cuda_devices: ["cuda:0"],
    mps_available: false,
    torch_available: true,
    torch_version: "2.0"
  },
  clip: {
    available: true,
    version: "test",
    error: null
  },
  default_model: "ViT-B-32",
  default_pretrained: "pretrained",
  token_required: true
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.chooseFolder.mockResolvedValue({ path: "/tmp/images", cancelled: false });
});

test("allows a local image or folder path to be typed or pasted", () => {
  const onSubmit = vi.fn();
  render(<ScanForm system={null} busy={false} onSubmit={onSubmit} />);

  const pathInput = screen.getByLabelText("Image or folder path");
  fireEvent.change(pathInput, { target: { value: "C:\\photos\\single-image.png" } });
  expect(pathInput).toHaveValue("C:\\photos\\single-image.png");
  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({ path: "C:\\photos\\single-image.png" })
  );
});

test("chooses a folder and submits default safe options", async () => {
  const onSubmit = vi.fn();
  render(<ScanForm system={null} busy={false} onSubmit={onSubmit} />);

  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  await waitFor(() => expect(mocks.chooseFolder).toHaveBeenCalledWith(""));
  expect(screen.getByLabelText("Image or folder path")).toHaveValue("/tmp/images");
  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      path: "/tmp/images",
      recursive: true,
      device: "auto",
      gpu_index: null,
      allow_clip_fallback: true,
      thresholds: expect.objectContaining({
        precision_mode: "conservative",
        phash_distance: 10,
        histogram_similarity: 0.9,
        clip_similarity: 0.92,
        performance_profile: "standard"
      })
    })
  );
});

test("applies selected precision preset to displayed values and submission", async () => {
  const onSubmit = vi.fn();
  render(<ScanForm system={null} busy={false} onSubmit={onSubmit} />);

  openAdvancedSettings();
  expect(valueInput("pHash distance")).toBe("10");
  expect(valueInput("Histogram")).toBe("0.90");
  expect(valueInput("CLIP cosine")).toBe("0.92");

  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  await waitFor(() => expect(screen.getByLabelText("Image or folder path")).toHaveValue("/tmp/images"));
  fireEvent.change(screen.getByLabelText("Precision mode"), {
    target: { value: "broad" }
  });
  expect(valueInput("pHash distance")).toBe("16");
  expect(valueInput("Histogram")).toBe("0.78");
  expect(valueInput("CLIP cosine")).toBe("0.88");

  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      thresholds: expect.objectContaining({
        precision_mode: "broad",
        phash_distance: 16,
        histogram_similarity: 0.78,
        clip_similarity: 0.88,
        performance_profile: "standard"
      })
    })
  );
});

test("applies ultra strict preset values", async () => {
  const onSubmit = vi.fn();
  render(<ScanForm system={null} busy={false} onSubmit={onSubmit} />);

  openAdvancedSettings();
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  await waitFor(() => expect(screen.getByLabelText("Image or folder path")).toHaveValue("/tmp/images"));
  fireEvent.change(screen.getByLabelText("Precision mode"), {
    target: { value: "ultra_strict" }
  });

  expect(valueInput("pHash distance")).toBe("2");
  expect(valueInput("Histogram")).toBe("0.99");
  expect(valueInput("CLIP cosine")).toBe("0.99");

  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      thresholds: expect.objectContaining({
        precision_mode: "ultra_strict",
        phash_distance: 2,
        histogram_similarity: 0.99,
        clip_similarity: 0.99,
        performance_profile: "standard"
      })
    })
  );
});

test("applies exact pixel preset values", async () => {
  const onSubmit = vi.fn();
  render(<ScanForm system={clipSystem} busy={false} onSubmit={onSubmit} />);

  openAdvancedSettings();
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  await waitFor(() => expect(screen.getByLabelText("Image or folder path")).toHaveValue("/tmp/images"));
  fireEvent.click(screen.getByLabelText("CLIP check"));
  expect(screen.getByLabelText("CLIP check")).toBeChecked();
  fireEvent.change(screen.getByLabelText("Precision mode"), {
    target: { value: "exact_pixel" }
  });

  expect(valueInput("pHash distance")).toBe("0");
  expect(valueInput("Histogram")).toBe("1.00");
  expect(valueInput("CLIP cosine")).toBe("1.00");
  expect(screen.getByLabelText("CLIP check")).not.toBeChecked();
  expect(screen.getByLabelText("CLIP check")).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      thresholds: expect.objectContaining({
        precision_mode: "exact_pixel",
        phash_distance: 0,
        histogram_similarity: 1,
        clip_similarity: 1,
        clip_enabled: false,
        performance_profile: "standard"
      })
    })
  );
});

test("reflects manual threshold edits from sliders and numeric inputs", async () => {
  const onSubmit = vi.fn();
  render(<ScanForm system={clipSystem} busy={false} onSubmit={onSubmit} />);

  openAdvancedSettings();
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  await waitFor(() => expect(screen.getByLabelText("Image or folder path")).toHaveValue("/tmp/images"));
  fireEvent.click(screen.getByLabelText("CLIP check"));
  fireEvent.change(screen.getByLabelText("pHash distance value"), {
    target: { value: "18" }
  });
  fireEvent.change(screen.getByLabelText("Histogram slider"), {
    target: { value: "0.83" }
  });
  fireEvent.change(screen.getByLabelText("CLIP cosine value"), {
    target: { value: "0.91" }
  });

  expect(valueInput("pHash distance")).toBe("18");
  expect(valueInput("Histogram")).toBe("0.83");
  expect(valueInput("CLIP cosine")).toBe("0.91");
  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      thresholds: expect.objectContaining({
        clip_enabled: true,
        phash_distance: 18,
        histogram_similarity: 0.83,
        clip_similarity: 0.91,
        performance_profile: "standard"
      })
    })
  );
});

test("lightweight profile disables clip and submits lightweight payload", async () => {
  const onSubmit = vi.fn();
  render(<ScanForm system={clipSystem} busy={false} onSubmit={onSubmit} />);

  openAdvancedSettings();
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  await waitFor(() => expect(screen.getByLabelText("Image or folder path")).toHaveValue("/tmp/images"));
  fireEvent.click(screen.getByLabelText("CLIP check"));
  expect(screen.getByLabelText("CLIP check")).toBeChecked();
  fireEvent.change(screen.getByLabelText("Performance profile"), {
    target: { value: "lightweight" }
  });

  expect(screen.getByLabelText("CLIP check")).not.toBeChecked();
  expect(screen.getByLabelText("CLIP check")).toBeDisabled();
  expect(screen.getByLabelText("Performance profile").closest("label")).toHaveAttribute(
    "title", expect.stringContaining("uses less CPU and memory")
  );

  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      thresholds: expect.objectContaining({
        clip_enabled: false,
        performance_profile: "lightweight"
      })
    })
  );
});

test("cancelled folder picker keeps start disabled", async () => {
  mocks.chooseFolder.mockResolvedValue({ path: null, cancelled: true });
  render(<ScanForm system={null} busy={false} onSubmit={vi.fn()} />);

  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));

  await waitFor(() => expect(mocks.chooseFolder).toHaveBeenCalled());
  expect(screen.getByLabelText("Image or folder path")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Start scan" })).toBeDisabled();
});

test("keeps advanced controls collapsed and explains why scanning cannot start", () => {
  render(<ScanForm system={clipSystem} busy={false} onSubmit={vi.fn()} />);

  expect(screen.queryByLabelText("CLIP check")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start scan" })).toHaveAttribute(
    "title", "Choose a folder or enter a local image path to start scanning."
  );
  expect(screen.getByLabelText("Precision mode").closest("label")).toHaveAttribute(
    "title", expect.stringContaining("Narrow visual search")
  );
  expect(screen.getByLabelText("Performance profile").closest("label")).toHaveAttribute(
    "title", expect.stringContaining("Uses the full evidence set")
  );

  openAdvancedSettings();

  expect(screen.getByLabelText("CLIP check")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Advanced settings" })).toHaveAttribute(
    "aria-expanded",
    "true"
  );
});

test("does not submit through Enter while scanning is disabled", () => {
  const onSubmit = vi.fn();
  render(<ScanForm system={clipSystem} busy={true} onSubmit={onSubmit} />);

  fireEvent.change(screen.getByLabelText("Image or folder path"), {
    target: { value: "C:\\photos" }
  });
  fireEvent.submit(screen.getByRole("button", { name: "Submitting" }).closest("form")!);

  expect(onSubmit).not.toHaveBeenCalled();
});

function valueInput(label: string): string {
  return (screen.getByLabelText(`${label} value`) as HTMLInputElement).value;
}

function openAdvancedSettings(): void {
  fireEvent.click(screen.getByRole("button", { name: "Advanced settings" }));
}
