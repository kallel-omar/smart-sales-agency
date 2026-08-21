import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp } from "./test/renderApp";

describe("HIRI public homepage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders the public homepage outside the authenticated app shell", () => {
    renderApp("/");

    expect(screen.getByRole("heading", { name: /hire your ai workforce/i })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "HIRI" }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("HIRI").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /book a demo/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /log in/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole("contentinfo")).toHaveTextContent("© 2026 HIRI");
    expect(screen.queryByRole("navigation", { name: /primary navigation/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Smart Sales Agency")).not.toBeInTheDocument();
  });

  it("provides real public routes and preserves the existing homepage content", () => {
    renderApp("/");

    const navigation = screen.getByRole("navigation", { name: /public navigation/i });
    expect(within(navigation).getByRole("link", { name: "Platform" })).toHaveAttribute("href", "/platform");
    expect(within(navigation).getByRole("link", { name: "Sales" })).toHaveAttribute("href", "/sales");
    expect(within(navigation).getByRole("link", { name: "How it works" })).toHaveAttribute(
      "href",
      "/how-it-works"
    );
    expect(within(navigation).getByRole("link", { name: "About" })).toHaveAttribute("href", "/about");
    expect(screen.getAllByRole("link", { name: "Create account" })[0]).toHaveAttribute("href", "/register");
    expect(screen.getByRole("heading", { name: /one platform for your ai workforce/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /start with sales/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /from business event to accountable result/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /the operating layer for ai business work/i })).toBeInTheDocument();
  });

  it("opens and closes the accessible mobile navigation", async () => {
    const user = userEvent.setup();
    renderApp("/");

    const toggle = screen.getByRole("button", { name: /open navigation/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);

    expect(screen.getByRole("button", { name: /close navigation/i })).toHaveAttribute(
      "aria-expanded",
      "true"
    );
    const mobileNavigation = screen.getByRole("navigation", { name: /mobile navigation/i });
    await user.click(within(mobileNavigation).getByRole("link", { name: "Sales" }));
    expect(screen.queryByRole("navigation", { name: /mobile navigation/i })).not.toBeInTheDocument();
  });

  it("keeps authenticated application routes protected and separate", async () => {
    renderApp("/app/workforce");

    expect(
      await screen.findByRole("heading", { name: /sign in to your workspace/i })
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /hire your ai workforce/i })).not.toBeInTheDocument();
  });
});
