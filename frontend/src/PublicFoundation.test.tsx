import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp } from "./test/renderApp";
import { fixtures, installFetchMock, mockJson } from "./test/mockApi";

describe("HIRI Phase 1 public foundation", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    ["/", "Hire your AI workforce."],
    ["/platform", "A controlled platform for AI business work"],
    ["/how-it-works", "From business request to accountable result"],
    ["/sales", "Operate Sales work through specialized AI employees"],
    ["/about", "Built for accountable AI operations"],
    ["/contact", "Talk with HIRI"],
    ["/login", "Sign in to your workspace"],
    ["/register", "Create your HIRI account"]
  ])("renders the public route %s", (path, heading) => {
    renderApp(path);
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  });

  it("provides the shared branding, route navigation and registration CTA", async () => {
    const user = userEvent.setup();
    renderApp("/");
    const navigation = screen.getByRole("navigation", { name: "Public navigation" });
    expect(screen.getAllByRole("link", { name: "HIRI" }).length).toBeGreaterThan(0);
    expect(within(navigation).getByRole("link", { name: "Platform" })).toHaveAttribute("href", "/platform");
    expect(within(navigation).getByRole("link", { name: "How it works" })).toHaveAttribute("href", "/how-it-works");
    expect(within(navigation).getByRole("link", { name: "Sales" })).toHaveAttribute("href", "/sales");
    expect(within(navigation).getByRole("link", { name: "About" })).toHaveAttribute("href", "/about");
    expect(screen.getAllByRole("link", { name: "Log in" }).length).toBeGreaterThan(0);
    const createAccount = screen.getAllByRole("link", { name: "Create account" })[0];
    expect(createAccount).toHaveAttribute("href", "/register");
    await user.click(createAccount);
    expect(screen.getByRole("heading", { name: "Create your HIRI account" })).toBeInTheDocument();
  });

  it("switches EN to FR to AR in place, sets document direction and restores LTR", async () => {
    const user = userEvent.setup();
    renderApp("/how-it-works");
    expect(document.documentElement).toHaveAttribute("lang", "en");
    expect(document.documentElement).toHaveAttribute("dir", "ltr");

    await user.selectOptions(screen.getByRole("combobox", { name: "Interface language" }), "fr");
    expect(screen.getByRole("heading", { name: "De la demande métier au résultat traçable" })).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("lang", "fr");
    expect(document.documentElement).toHaveAttribute("dir", "ltr");

    await user.selectOptions(screen.getByRole("combobox", { name: "Langue de l’interface" }), "ar");
    expect(screen.getByRole("heading", { name: "من طلب العمل إلى نتيجة قابلة للمساءلة" })).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("lang", "ar");
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(document.querySelector('img[src="/hiri-logo.svg"]')).not.toHaveStyle({ transform: "scaleX(-1)" });

    await user.selectOptions(screen.getByRole("combobox", { name: "لغة الواجهة" }), "en");
    expect(screen.getByRole("heading", { name: "From business request to accountable result" })).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "ltr");
    expect(localStorage.getItem("hiri-locale")).toBe("en");
  });

  it("registers through the existing API, logs in and enters the protected app", async () => {
    const user = userEvent.setup();
    const fetchMock = installFetchMock((url, init) => {
      if (url.endsWith("/api/auth/register")) return mockJson(fixtures.user, 201);
      if (url.endsWith("/api/auth/login")) return mockJson(fixtures.token);
      if (url.endsWith("/api/auth/me")) return mockJson(fixtures.user);
      if (url.endsWith("/api/workspaces")) return mockJson([]);
      return mockJson({ detail: `Unhandled ${url}` }, 500);
    });
    renderApp("/register");
    const main = screen.getByRole("main");
    await user.type(within(main).getByLabelText("Display name"), "HIRI Admin");
    await user.type(within(main).getByLabelText("Email"), "admin@hiri.local");
    await user.type(within(main).getByLabelText("Password"), "correct-password");
    await user.type(within(main).getByLabelText("Confirm password"), "correct-password");
    await user.click(within(main).getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(localStorage.getItem("hiri.auth.accessToken")).toBe(fixtures.token.access_token);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/auth/register"))).toBe(true));
    const registration = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/auth/register"));
    expect(JSON.parse(registration?.[1]?.body as string)).toEqual({ email: "admin@hiri.local", password: "correct-password", display_name: "HIRI Admin" });
  });
});
