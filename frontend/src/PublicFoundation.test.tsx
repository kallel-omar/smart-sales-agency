import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp } from "./test/renderApp";
import { fixtures, installFetchMock, mockJson } from "./test/mockApi";

describe("HIRI complete public experience", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    ["/", "Hire your AI workforce."],
    ["/platform", "The operating system for an AI workforce."],
    ["/how-it-works", "From business event to governed execution."],
    ["/sales", "AI sales employees for governed revenue work."],
    ["/about", "Make AI work useful, controllable and accountable."],
    ["/contact", "Choose the right way to begin with HIRI."],
    ["/login", "Sign in to your workspace"],
    ["/register", "Create your HIRI account"]
  ])("renders the dedicated public route %s", (path, heading) => {
    renderApp(path);
    expect(screen.getByRole("heading", { name: heading, level: 1 })).toBeInTheDocument();
  });

  it("provides shared route navigation, access links, theme control and a factual footer", async () => {
    const user = userEvent.setup(); renderApp("/platform");
    const navigation = screen.getByRole("navigation", { name: "Public navigation" });
    expect(within(navigation).getByRole("link", { name: "Platform" })).toHaveAttribute("aria-current", "page");
    expect(within(navigation).getByRole("link", { name: "How it works" })).toHaveAttribute("href", "/how-it-works");
    expect(within(navigation).getByRole("link", { name: "Sales" })).toHaveAttribute("href", "/sales");
    expect(screen.getAllByRole("link", { name: "Create account" })[0]).toHaveAttribute("href", "/register");
    expect(screen.getByRole("contentinfo")).toHaveTextContent("AI workforce. Human control.");
    await user.click(screen.getByRole("button", { name: "Dark theme" }));
    expect(document.documentElement).toHaveAttribute("data-hiri-theme", "dark");
    expect(localStorage.getItem("hiri-theme")).toBe("dark");
  });

  it.each([
    ["/", "One platform. AI employees. Real business execution.", "Une plateforme. Des employés IA. Une exécution métier réelle.", "منصة واحدة. موظفون بالذكاء الاصطناعي. تنفيذ حقيقي للأعمال."],
    ["/platform", "Every employee has an explicit operating identity.", "Chaque employé possède une identité opérationnelle explicite.", "لكل موظف هوية تشغيلية واضحة."],
    ["/how-it-works", "Twelve checks, one understandable path.", "Douze contrôles, un parcours compréhensible.", "اثنتا عشرة نقطة تحقق ضمن مسار واضح."],
    ["/sales", "Turn signals of intent into structured work.", "Transformez les signaux d’intention en travail structuré.", "حوّل إشارات الاهتمام إلى عمل منظم."],
    ["/login", "Sign in to your workspace", "Connectez-vous à votre espace de travail", "سجّل الدخول إلى مساحة عملك"],
    ["/register", "Create your HIRI account", "Créez votre compte HIRI", "أنشئ حسابك في HIRI"]
  ])("localizes representative content on %s from EN to FR to AR", async (path, english, french, arabic) => {
    const user = userEvent.setup(); renderApp(path);
    expect(screen.getByRole("heading", { name: english })).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Interface language" }), "fr");
    expect(screen.getByRole("heading", { name: french })).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Langue de l’interface" }), "ar");
    expect(screen.getByRole("heading", { name: arabic })).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("lang", "ar");
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(document.querySelector('img[src="/hiri-logo.svg"]')).not.toHaveStyle({ transform: "scaleX(-1)" });
  });

  it("states that Contact does not submit data instead of faking a workflow", () => {
    renderApp("/contact");
    expect(screen.getByText(/No form on this page submits data today\./)).toBeInTheDocument();
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
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
    renderApp("/register"); const main = screen.getByRole("main");
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
