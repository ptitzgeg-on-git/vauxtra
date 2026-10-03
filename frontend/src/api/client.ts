import { HttpError } from './httpError';

export { HttpError } from './httpError';

/**
 * The API root every call is relative to, without its trailing slash.
 *
 * Exported because `EventSource` (the log stream) cannot go through this client and would
 * otherwise hardcode `/api`, which is wrong for any build configured with `VITE_API_URL`.
 */
export const API_BASE_URL: string = (import.meta.env.VITE_API_URL || '/api').replace(/\/+$/, '');

/** The options a call can pass. These are the only ones the panel uses. */
export interface RequestConfig {
  signal?: AbortSignal;
  /** `blob` for a file download. The error body of such a call is a Blob too: see `decodeBlobErrorBody`. */
  responseType?: 'json' | 'blob';
  headers?: Record<string, string>;
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}

async function readBody(res: Response, responseType: RequestConfig['responseType']): Promise<unknown> {
  if (responseType === 'blob') return res.blob();
  const text = await res.text();
  if (!text) return undefined;
  // FastAPI answers JSON; a proxy in front of it may answer an HTML or text error page.
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function request<T>(method: string, url: string, data: unknown, config: RequestConfig = {}): Promise<T> {
  const headers: Record<string, string> = { ...config.headers };
  if (data !== undefined) headers['Content-Type'] ??= 'application/json';

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${url}`, {
      method,
      headers,
      body: data === undefined ? undefined : JSON.stringify(data),
      credentials: 'include',
      signal: config.signal,
    });
  } catch (err) {
    if (isAbort(err)) throw err;
    throw new HttpError('Network Error', url);
  }

  const body = await readBody(res, config.responseType);
  if (res.ok) return body as T;

  const error = new HttpError(`Request failed with status code ${res.status}`, url, {
    status: res.status,
    data: body,
    headers: Object.fromEntries(res.headers.entries()),
  });
  // A 401 outside the auth routes means the session expired: AuthGate shows the login.
  if (res.status === 401 && !url.includes('/auth/')) {
    window.dispatchEvent(new CustomEvent('vauxtra:auth-expired'));
  }
  if (import.meta.env.DEV) {
    console.error('API Error:', body ?? error);
  }
  throw error;
}

export const api = {
  get<T = unknown>(url: string, config?: RequestConfig): Promise<T> {
    return request<T>('GET', url, undefined, config);
  },
  post<T = unknown>(url: string, data?: unknown, config?: RequestConfig): Promise<T> {
    return request<T>('POST', url, data, config);
  },
  put<T = unknown>(url: string, data?: unknown, config?: RequestConfig): Promise<T> {
    return request<T>('PUT', url, data, config);
  },
  patch<T = unknown>(url: string, data?: unknown, config?: RequestConfig): Promise<T> {
    return request<T>('PATCH', url, data, config);
  },
  delete<T = unknown>(url: string, config?: RequestConfig): Promise<T> {
    return request<T>('DELETE', url, undefined, config);
  },
};
