export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function friendly(status: number, detail: string | undefined): string {
  if (status === 404) return detail && detail.length < 90 ? detail : "The requested record was not found.";
  if (status === 409) return "The underlying data changed since this preview. This approval is no longer valid — run a fresh investigation before continuing.";
  if (status === 422) return "That request could not be accepted. Review the current preview and try again.";
  if (status === 503) return "The investigation service is currently unavailable. Your evidence is still available; please try again later.";
  if (status >= 500) return "Something went wrong while contacting the local service. Please try again.";
  return detail && detail.length < 120 && !detail.includes("/") ? detail : "The request could not be completed.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "Cannot reach the local AfterFix service. Check that it is running, then try again.");
  }
  const text = await res.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in data && typeof (data as { detail: unknown }).detail === "string"
      ? (data as { detail: string }).detail : undefined;
    throw new ApiError(res.status, friendly(res.status, detail));
  }
  return data as T;
}

export const apiGet = <T,>(path: string) => request<T>(path);
export const apiPost = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });
