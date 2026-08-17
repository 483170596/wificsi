import type { NodesResponse } from "./contracts";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`GET ${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export function fetchNodes(): Promise<NodesResponse> {
  return getJson<NodesResponse>("/api/v1/nodes");
}
