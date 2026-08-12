import { apiClient } from "./api";
import { installFetchMock, mockJson } from "../test/mockApi";

describe("apiClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("attaches authorization authentication", async () => {
    const fetchMock = installFetchMock(() => mockJson([]));
    const accessToken = "access-token";

    await apiClient.workspaces(accessToken);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/workspaces");
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe(`Bearer ${accessToken}`);
  });

  it("attaches the selected workspace header to scoped requests", async () => {
    const fetchMock = installFetchMock(() => mockJson([]));

    await apiClient.leads("access-token", "company-a");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("X-Workspace-Slug")).toBe("company-a");
  });

  it("fetches conversation history through the workspace-scoped FastAPI route", async () => {
    const fetchMock = installFetchMock(() => mockJson([]));
    const accessToken = "access-token";

    await apiClient.conversationHistory(accessToken, "company-a", "lead-1", 25);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/conversations/lead-1?limit=25");
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe(`Bearer ${accessToken}`);
    expect(headers.get("X-Workspace-Slug")).toBe("company-a");
  });

  it("posts conversation replies with workspace and idempotency headers", async () => {
    const fetchMock = installFetchMock(() => mockJson({}));
    const accessToken = "access-token";

    await apiClient.replyToConversation({
      token: accessToken,
      workspaceSlug: "company-a",
      leadId: "lead-1",
      content: "Customer asked for pricing.",
      channel: "whatsapp_cloud",
      idempotencyKey: "idem-1"
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/conversations/lead-1/reply");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe(`Bearer ${accessToken}`);
    expect(headers.get("X-Workspace-Slug")).toBe("company-a");
    expect(headers.get("Idempotency-Key")).toBe("idem-1");
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual({
      channel: "whatsapp_cloud",
      content: "Customer asked for pricing."
    });
  });

  it("keeps conversation calls on relative FastAPI API paths", async () => {
    const fetchMock = installFetchMock(() => mockJson([]));
    const accessToken = "access-token";

    await apiClient.conversationHistory(accessToken, "company-a", "lead-1");
    await apiClient.replyToConversation({
      token: accessToken,
      workspaceSlug: "company-a",
      leadId: "lead-1",
      content: "Customer asked for next steps.",
      channel: "console",
      idempotencyKey: "idem-2"
    });

    const requestedUrls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(requestedUrls.every((url) => url.startsWith("/api/"))).toBe(true);
    expect(requestedUrls.some((url) => url.startsWith("http://") || url.startsWith("https://"))).toBe(false);
  });
});
