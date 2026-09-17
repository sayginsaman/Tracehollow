export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfterSeconds: number | null;
  readonly payload: unknown;

  constructor(
    status: number,
    code: string,
    retryAfterSeconds: number | null = null,
    payload: unknown = null,
  ) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
    this.payload = payload;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** Multipart body (evidence uploads). The browser sets the boundary header. */
  form?: FormData;
  csrfToken?: string;
}

function extractCode(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message: unknown }).message;
      if (typeof message === "string") return message;
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first.msg === "string") return first.msg;
    }
  }
  return status === 502 || status === 504 ? "api_unreachable" : `http_${status}`;
}

/** Same-origin JSON request through the web proxy. Throws ApiError on any failure. */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined && options.form === undefined) headers["Content-Type"] = "application/json";
  if (options.csrfToken) headers["X-CSRF-Token"] = options.csrfToken;
  const body = options.form ?? (options.body === undefined ? undefined : JSON.stringify(options.body));

  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      headers,
      body,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "network_error");
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const retryAfter = Number.parseInt(response.headers.get("retry-after") ?? "", 10);
    throw new ApiError(
      response.status,
      extractCode(payload, response.status),
      Number.isFinite(retryAfter) ? retryAfter : null,
      payload,
    );
  }
  return payload as T;
}
