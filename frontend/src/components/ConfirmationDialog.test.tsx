import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmationDialog } from "@/components/ConfirmationDialog";

describe("ConfirmationDialog", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <ConfirmationDialog
        open={false}
        title="Confirm"
        onConfirm={() => undefined}
        onCancel={() => undefined}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the title and description when open", () => {
    render(
      <ConfirmationDialog
        open
        title="Queue scan?"
        description="This will fetch the repository."
        onConfirm={() => undefined}
        onCancel={() => undefined}
      />
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Queue scan?")).toBeInTheDocument();
    expect(screen.getByText("This will fetch the repository.")).toBeInTheDocument();
  });

  it("invokes the cancel handler when the user presses Escape", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(
      <ConfirmationDialog
        open
        title="Cancel?"
        onConfirm={() => undefined}
        onCancel={onCancel}
      />
    );
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("invokes the confirm handler when the user clicks Confirm", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ConfirmationDialog
        open
        title="Queue scan?"
        onConfirm={onConfirm}
        onCancel={() => undefined}
      />
    );
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("uses destructive styling when destructive is true", () => {
    render(
      <ConfirmationDialog
        open
        title="Delete"
        confirmLabel="Delete"
        destructive
        onConfirm={() => undefined}
        onCancel={() => undefined}
      />
    );
    const button = screen.getByRole("button", { name: /delete/i });
    // The destructive surface moved out of this component into the
    // shared ``.btn-danger`` primitive so the solid rose stays
    // legible under white label text in both themes. The contract
    // the test guards is unchanged: destructive => danger styling,
    // non-destructive => the primary styling.
    expect(button.className).toContain("btn-danger");
    expect(button.className).not.toContain("btn-primary");
  });
});
