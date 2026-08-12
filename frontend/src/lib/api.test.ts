import { apiClient } from "./api";
import { installFetchMock, mockJson } from "../test/mockApi";

describe("apiClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("attaches bearer authentication", async () => {
    const fetchMock = installFetchMock(() => mockJson([]));

    await apiClient.workspaces("access-token");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/workspaces");
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer access-token");
  });

  it("attaches the selected workspace header to scoped requests", async () => {
    const fetchMock = installFetchMock(() => mockJson([]));

    await apiClient.leads("access-token", "company-a");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("X-Workspace-Slug")).toBe("company-a");
  });
});
