import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp } from "./test/renderApp";
import { analyticsFixture, fixtures, installFetchMock, mockJson } from "./test/mockApi";

const workItem = {
  id: "work-rtl-1", title: "Relance client — ne pas traduire", work_type: "sales_follow_up_message",
  status: "approval_required", department: "sales", department_id: "department-1",
  ai_employee_id: "employee-1", ai_employee_name: "Follow-up Specialist", capability_id: "capability-1",
  capability_key: "follow_up_lead", input: { email: "client@example.fr" }, result: null,
  error_code: null, error_message: null, correlation_id: "corr-ltr-123", parent_work_item_id: null,
  source_follow_up_task_id: null, approval_id: "approval-1", approval_status: "pending",
  created_at: "2026-08-20T10:00:00Z", updated_at: "2026-08-20T10:01:00Z",
  started_at: null, completed_at: null
};

function installExperienceApi(workspaces = fixtures.workspaces) {
  return installFetchMock((url) => {
    if (url.endsWith("/api/auth/me")) return mockJson(fixtures.user);
    if (url.endsWith("/api/workspaces")) return mockJson(workspaces);
    if (url.endsWith("/api/leads")) return mockJson([]);
    if (url.includes("/api/integrations/accounts")) return mockJson([]);
    if (url.includes("/api/integrations/operations-summary")) return mockJson({ delivered_outbound_action_count: 0, failed_outbound_action_count: 0 });
    if (url.includes("/api/operator/analytics")) return mockJson(analyticsFixture);
    if (url.includes("/api/operator/work-items")) return mockJson([workItem]);
    if (url.includes("/api/operator/approvals")) return mockJson([]);
    if (url.includes("/api/operator/workforce")) return mockJson([]);
    return mockJson({ detail: `Unhandled ${url}` }, 500);
  });
}

describe("authenticated HIRI experience", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("hiri.auth.accessToken", "test-token");
    installExperienceApi();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("switches light and dark themes without leaving the current page and persists the choice", async () => {
    const user = userEvent.setup();
    const { container } = renderApp("/app");
    const app = await screen.findByRole("link", { name: "Dashboard" });
    expect(app).toBeInTheDocument();
    expect(container.querySelector(".hiri-app")).toHaveAttribute("data-theme", "light");
    const toggle = screen.getByRole("button", { name: "Switch color theme" });
    await user.click(toggle);
    expect(container.querySelector(".hiri-app")).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem("hiri-theme")).toBe("dark");
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/app");
    await user.click(toggle);
    expect(container.querySelector(".hiri-app")).toHaveAttribute("data-theme", "light");
  });

  it("loads saved theme while keeping the public homepage outside authenticated theming", async () => {
    localStorage.setItem("hiri-theme", "dark");
    const authenticated = renderApp("/app/analytics");
    await screen.findByRole("link", { name: "Analytics" });
    expect(authenticated.container.querySelector(".hiri-app")).toHaveAttribute("data-theme", "dark");
    authenticated.unmount();
    const publicPage = renderApp("/");
    expect(await screen.findByRole("heading", { name: /hire your ai workforce/i })).toBeInTheDocument();
    expect(publicPage.container.querySelector(".hiri-app")).not.toBeInTheDocument();
  });

  it("switches the operator interface to French and persists the locale", async () => {
    const user = userEvent.setup();
    const view = renderApp("/app");
    await screen.findByRole("link", { name: "Dashboard" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Interface language" }), "fr");
    expect(screen.getByRole("link", { name: "Tableau de bord" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Boîte commerciale" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Centre de commande de l’équipe IA" })).toBeInTheDocument();
    expect(view.container.querySelector(".hiri-app")).toHaveAttribute("dir", "ltr");
    expect(localStorage.getItem("hiri-locale")).toBe("fr");
  });

  it("provides real Arabic RTL shell behavior and returns cleanly to English LTR", async () => {
    const user = userEvent.setup();
    const { container } = renderApp("/app");
    await screen.findByRole("link", { name: "Dashboard" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Interface language" }), "ar");
    const shell = container.querySelector(".hiri-app");
    expect(shell).toHaveAttribute("dir", "rtl");
    expect(shell).toHaveAttribute("lang", "ar");
    expect(screen.getByRole("link", { name: "لوحة التحكم" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "مهام العمل" })).toBeInTheDocument();
    expect(screen.getByText("operator@example.test")).toHaveAttribute("dir", "ltr");
    expect(container.querySelector('img[src="/hiri-logo.svg"]')).not.toHaveStyle({ transform: "scaleX(-1)" });
    await user.selectOptions(screen.getByRole("combobox", { name: "لغة الواجهة" }), "en");
    expect(shell).toHaveAttribute("dir", "ltr");
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("switches representative Dashboard UI strings EN to FR to AR without translating the workspace name", async () => {
    const user = userEvent.setup();
    installExperienceApi([{ ...fixtures.workspaces[0], name: "HIRI Local" }]);
    const { container } = renderApp("/app");

    expect(await screen.findByText("Configured workforce")).toBeInTheDocument();
    expect(screen.getAllByText("HIRI Local").length).toBeGreaterThan(0);

    await user.selectOptions(screen.getByRole("combobox", { name: "Interface language" }), "fr");
    expect(screen.getByText("Équipe configurée")).toBeInTheDocument();
    expect(screen.getByText("Actions livrées")).toBeInTheDocument();
    expect(screen.getAllByText("HIRI Local").length).toBeGreaterThan(0);

    await user.selectOptions(screen.getByRole("combobox", { name: "Langue de l’interface" }), "ar");
    expect(screen.getByText("القوى العاملة المهيأة")).toBeInTheDocument();
    expect(screen.getByText("الإجراءات المنفّذة")).toBeInTheDocument();
    expect(container.querySelector(".hiri-app")).toHaveAttribute("lang", "ar");
    expect(container.querySelector(".hiri-app")).toHaveAttribute("dir", "rtl");
    expect(screen.getAllByText("HIRI Local").length).toBeGreaterThan(0);
  });

  it("switches representative Analytics UI strings EN to FR to AR", async () => {
    const user = userEvent.setup();
    const { container } = renderApp("/app/analytics");

    expect(await screen.findByText("WorkItems created")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Last 30 days" })).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Interface language" }), "fr");
    expect(screen.getByText("WorkItems créés")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "30 derniers jours" })).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Langue de l’interface" }), "ar");
    expect(screen.getByText("WorkItems المنشأة")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "آخر 30 يومًا" })).toBeInTheDocument();
    expect(container.querySelector(".hiri-app")).toHaveAttribute("lang", "ar");
    expect(container.querySelector(".hiri-app")).toHaveAttribute("dir", "rtl");
  });

  it("localizes the genuine no-workspace state in English, French and Arabic", async () => {
    const user = userEvent.setup();
    installExperienceApi([]);
    renderApp("/app");

    expect(await screen.findByText("No workspace available")).toBeInTheDocument();
    expect(screen.getByText("Create or join a workspace before using the operating dashboard.")).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Interface language" }), "fr");
    expect(screen.getByText("Aucun espace de travail disponible")).toBeInTheDocument();
    expect(screen.getByText("Créez ou rejoignez un espace de travail avant d’utiliser le tableau de bord opérationnel.")).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Langue de l’interface" }), "ar");
    expect(screen.getByText("لا توجد مساحة عمل متاحة")).toBeInTheDocument();
    expect(screen.getByText("أنشئ مساحة عمل أو انضم إليها قبل استخدام لوحة التحكم التشغيلية.")).toBeInTheDocument();
  });

  it("keeps raw WorkItem values unchanged in Arabic filters and detail", async () => {
    const user = userEvent.setup();
    localStorage.setItem("hiri-locale", "ar");
    const { container } = renderApp("/app/work-items");
    const rawTitle = await screen.findByRole("button", { name: workItem.title });
    expect(container.querySelector(".hiri-app")).toHaveAttribute("dir", "rtl");
    expect(screen.getByLabelText("الحالة")).toBeInTheDocument();
    expect(rawTitle).toHaveAttribute("dir", "auto");
    await user.click(rawTitle);
    const dialog = screen.getByRole("dialog", { name: "تفاصيل WorkItem" });
    expect(within(dialog).getByText("client@example.fr")).toBeInTheDocument();
    expect(within(dialog).getByText("corr-ltr-123")).toBeInTheDocument();
  });
});
