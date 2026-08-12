import type { Mock } from "vitest";

type FetchMock = Mock<typeof fetch>;

const jsonHeaders = { "Content-Type": "application/json" };

export function mockJson(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: jsonHeaders });
}

export function installFetchMock(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    return Promise.resolve(handler(url, init));
  }) as FetchMock;
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const fixtures = {
  token: { access_token: "test-access-token", token_type: "bearer", expires_in: 3600 },
  user: {
    id: "user-1",
    email: "operator@example.test",
    display_name: "Operator",
    active: true,
    created_at: "2026-08-12T00:00:00Z",
    updated_at: "2026-08-12T00:00:00Z"
  },
  workspaces: [
    {
      id: "workspace-1",
      slug: "workspace-a",
      name: "Workspace A",
      active: true,
      created_at: "2026-08-12T00:00:00Z",
      updated_at: "2026-08-12T00:00:00Z"
    },
    {
      id: "workspace-2",
      slug: "workspace-b",
      name: "Workspace B",
      active: true,
      created_at: "2026-08-12T00:00:00Z",
      updated_at: "2026-08-12T00:00:00Z"
    }
  ]
};
