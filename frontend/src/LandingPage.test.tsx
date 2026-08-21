import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp } from "./test/renderApp";

describe("HIRI public homepage", () => {
  beforeEach(() => localStorage.clear());

  it("presents the complete homepage narrative outside the app shell", () => {
    renderApp("/");
    expect(screen.getByRole("heading", { name: "Hire your AI workforce." })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "One platform. AI employees. Real business execution." })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "From demand to an accountable result." })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "A mature starting point for the AI workforce." })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Authority should be earned, not assumed." })).toBeInTheDocument();
    expect(screen.getByLabelText("Governed execution")).toHaveTextContent("WI-SALES-014");
    expect(screen.queryByText(/testimonial/i)).not.toBeInTheDocument();
  });

  it("uses dedicated product routes instead of homepage anchors", () => {
    renderApp("/");
    const navigation = screen.getByRole("navigation", { name: "Public navigation" });
    expect(within(navigation).getByRole("link", { name: "Platform" })).toHaveAttribute("href", "/platform");
    expect(within(navigation).getByRole("link", { name: "How it works" })).toHaveAttribute("href", "/how-it-works");
    expect(within(navigation).getByRole("link", { name: "Sales" })).toHaveAttribute("href", "/sales");
    expect(screen.getAllByRole("link", { name: "See how it works" })[0]).toHaveAttribute("href", "/how-it-works");
    expect(screen.getAllByRole("link", { name: "Explore HIRI Sales" })[0]).toHaveAttribute("href", "/sales");
    expect(screen.getAllByRole("link", { name: "Create account" })[0]).toHaveAttribute("href", "/register");
    expect(within(navigation).queryByRole("link", { name: "Platform" })).not.toHaveAttribute("href", expect.stringContaining("#"));
  });

  it("opens and closes the accessible mobile navigation", async () => {
    const user = userEvent.setup(); renderApp("/");
    const toggle = screen.getByRole("button", { name: "Open navigation" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    const mobile = screen.getByRole("navigation", { name: "Mobile navigation" });
    expect(screen.getByRole("button", { name: "Close navigation" })).toHaveAttribute("aria-expanded", "true");
    await user.click(within(mobile).getByRole("link", { name: "Sales" }));
    expect(screen.queryByRole("navigation", { name: "Mobile navigation" })).not.toBeInTheDocument();
  });

  it("keeps authenticated routes protected and separate", async () => {
    renderApp("/app/workforce");
    expect(await screen.findByRole("heading", { name: "Sign in to your workspace" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Hire your AI workforce." })).not.toBeInTheDocument();
  });
});
