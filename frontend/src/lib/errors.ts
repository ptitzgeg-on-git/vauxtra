/**
 * Reading an error the way FastAPI writes it.
 *
 * Every route raises `HTTPException(status, detail)`; `detail` is a string most of the
 * time, a list of `{loc, msg, type}` when pydantic rejected the body (422), and an object
 * with a `message` when a route wants to hand back structure as well (the 409 on provider
 * deletion carries the dependent services). Pages used to reach into
 * `err.response.data.detail` by hand at every call site and show whatever was there, so a
 * validation error rendered as `[object Object]`. This is the one reader.
 */

import axios, { type AxiosError } from 'axios';

/** One entry of a pydantic 422 body. */
export interface ApiValidationErrorItem {
  loc?: Array<string | number>;
  msg: string;
  type?: string;
  input?: unknown;
}

/** A structured `detail`, e.g. the 409 on `DELETE /api/providers/{pid}`. */
export interface ApiErrorObjectDetail {
  message?: string;
  [key: string]: unknown;
}

export type ApiErrorDetail = string | ApiValidationErrorItem[] | ApiErrorObjectDetail;

export interface ApiErrorBody {
  detail?: ApiErrorDetail;
  message?: string;
  error?: string;
}

/** An axios rejection whose body follows the FastAPI convention. */
export type ApiError = AxiosError<ApiErrorBody>;

export function isApiError(err: unknown): err is ApiError {
  return axios.isAxiosError(err);
}

/** The HTTP status of a failed request, `undefined` for network errors and plain throws. */
export function getHttpStatus(err: unknown): number | undefined {
  return isApiError(err) ? err.response?.status : undefined;
}

/** `isHttpStatus(err, 404)` or `isHttpStatus(err, [401, 403])`. */
export function isHttpStatus(err: unknown, code: number | readonly number[]): boolean {
  const status = getHttpStatus(err);
  if (status === undefined) return false;
  return typeof code === 'number' ? status === code : code.includes(status);
}

/** True when the request never reached the server (DNS, refused, timeout, offline). */
export function isNetworkError(err: unknown): boolean {
  return isApiError(err) && !err.response && !axios.isCancel(err);
}

/** True when the caller aborted the request (an `AbortSignal` or a cancel token). */
export function isCanceledError(err: unknown): boolean {
  return axios.isCancel(err) || (err instanceof DOMException && err.name === 'AbortError');
}

/** The raw `detail` of a failed request, whatever its shape. */
export function getErrorDetail(err: unknown): ApiErrorDetail | undefined {
  if (!isApiError(err)) return undefined;
  const body = err.response?.data;
  if (!body || typeof body !== 'object') return undefined;
  return (body as ApiErrorBody).detail;
}

// pydantic prefixes a `ValueError` raised in a validator with "Value error, ".
const PYDANTIC_PREFIX = /^(?:Value|Assertion) error,\s*/i;
// The first `loc` segment names where the field lives, not the field.
const LOC_ROOTS = new Set(['body', 'query', 'path', 'header', 'cookie']);

function validationItemToText(item: ApiValidationErrorItem): string {
  const msg = String(item.msg ?? '').replace(PYDANTIC_PREFIX, '').trim();
  const loc = Array.isArray(item.loc) ? item.loc.map(String) : [];
  const field = (loc.length > 1 && LOC_ROOTS.has(loc[0]) ? loc.slice(1) : loc).join('.');
  return field ? `${field}: ${msg}` : msg;
}

/** A single sentence from a `detail` of any shape, or `undefined` when it holds none. */
export function detailToMessage(detail: ApiErrorDetail | undefined): string | undefined {
  if (detail === undefined || detail === null) return undefined;
  if (typeof detail === 'string') {
    const text = detail.trim();
    return text || undefined;
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .filter((item): item is ApiValidationErrorItem => Boolean(item) && typeof item === 'object')
      .map(validationItemToText)
      .filter(Boolean);
    return parts.length ? parts.join('; ') : undefined;
  }
  if (typeof detail === 'object') {
    const message = (detail as ApiErrorObjectDetail).message;
    if (typeof message === 'string' && message.trim()) return message.trim();
    const nested = (detail as { detail?: unknown }).detail;
    if (typeof nested === 'string' && nested.trim()) return nested.trim();
  }
  return undefined;
}

/**
 * `responseType: 'blob'` makes axios parse the *error* body as a Blob as well, so
 * `err.response.data` is a Blob and every reader below finds nothing in it. Await this
 * before reading such an error: it rewrites the body into the parsed JSON (or the plain
 * text) and hands the error back for chaining. A body it cannot decode is left as it was.
 */
export async function decodeBlobErrorBody(err: unknown): Promise<unknown> {
  if (!isApiError(err) || !err.response) return err;
  const body: unknown = err.response.data;
  if (typeof Blob === 'undefined' || !(body instanceof Blob)) return err;
  try {
    const text = (await body.text()).trim();
    if (!text) return err;
    const parsed: unknown = text.startsWith('{') || text.startsWith('[') ? JSON.parse(text) : text;
    err.response.data = parsed as ApiErrorBody;
  } catch {
    // Not JSON, or the Blob could not be read: keep the body as it came.
  }
  return err;
}

const AXIOS_GENERIC = /^Request failed with status code \d+$/;

/**
 * The backend's own words for a failed call, `fallback` when it said nothing usable.
 *
 * Order: `response.data.detail` (string, pydantic list joined as `field: msg`, or an object's
 * `message`), then `response.data.message` / `.error`, then the error's own `message`, then
 * `fallback`. axios's own "Request failed with status code N" is skipped in favour of the
 * fallback -- the caller's fallback is translated, that string is not.
 *
 * Every FastAPI route writes `detail` in English (`app/api/services.py`, `app/security.py`),
 * so this is NOT what a user-facing toast should read: use `translateApiError` for that and
 * keep this one for a "details" line next to the translated sentence, or for logging.
 */
export function getErrorMessage(err: unknown, fallback: string): string {
  if (isApiError(err)) {
    // Widened: the axios generic says ApiErrorBody, but a proxy may answer with plain text.
    const body: unknown = err.response?.data;
    if (body && typeof body === 'object') {
      const fromDetail = detailToMessage((body as ApiErrorBody).detail);
      if (fromDetail) return fromDetail;
      for (const key of ['message', 'error'] as const) {
        const value = (body as ApiErrorBody)[key];
        if (typeof value === 'string' && value.trim()) return value.trim();
      }
    } else if (typeof body === 'string' && body.trim() && body.trim().length < 300) {
      // A proxy in front of the API (nginx, Cloudflare) answers with text, not JSON.
      return body.trim();
    }
    if (err.message && !AXIOS_GENERIC.test(err.message)) return err.message;
    return fallback;
  }
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err.trim()) return err.trim();
  return fallback;
}

/** `t` as the i18n provider hands it out; typed here so `lib/` does not import the provider. */
export type Translate = (key: string, params?: Record<string, string | number>) => string;

/**
 * The status codes whose meaning the caller's `fallback` cannot express: the request did not
 * fail *because of this action*, it failed because the session, the quota or the server did.
 * Everything else (400, 404, 409, 422, ...) is about the action itself, and the caller's own
 * sentence -- "Failed to add domain" -- says it better than a generic one.
 */
function statusMessageKey(status: number): string | undefined {
  if (status === 401) return 'common.error.unauthorized';
  if (status === 403) return 'common.error.forbidden';
  if (status === 429) return 'common.error.rate_limited';
  if (status >= 500) return 'common.error.server';
  return undefined;
}

/**
 * The translated message to show for a failed call.
 *
 * `fallback` is already a `t()` sentence about the action that failed, so it wins over the
 * backend's `detail`, which is English on every route. Only a failure the fallback would
 * misdescribe gets its own words: a request that never reached the server, an expired
 * session, a refused scope, a rate limit, a 5xx. The raw `detail` stays reachable through
 * `getErrorMessage` for a details line, and the DEV console already logs it.
 */
export function translateApiError(err: unknown, t: Translate, fallback: string): string {
  if (isCanceledError(err)) return fallback;
  if (isNetworkError(err)) return t('common.error.network');
  const status = getHttpStatus(err);
  if (status !== undefined) {
    const key = statusMessageKey(status);
    return key ? t(key) : fallback;
  }
  // Not an HTTP failure at all (a thrown Error, a rejected promise): its own message is the
  // only thing that describes it.
  return getErrorMessage(err, fallback);
}

// `retry after 30 seconds`, `Retry in 2 minutes`, `try again in 1 minute`
const RETRY_TEXT = /(?:retry|try again)(?:\s+again)?\s+(?:in|after)\s+(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes)\b/i;

function readHeader(err: ApiError, name: string): string | undefined {
  const headers = err.response?.headers as Record<string, unknown> | undefined;
  if (!headers) return undefined;
  const raw = headers[name] ?? headers[name.toLowerCase()] ?? headers[name.toUpperCase()];
  if (raw === undefined || raw === null) return undefined;
  return Array.isArray(raw) ? String(raw[0]) : String(raw);
}

/**
 * Seconds to wait before retrying a 429/503, from the `Retry-After` header (delay or HTTP
 * date) or a "retry in N seconds" phrase in the detail. `null` when nothing says.
 */
export function getRetryAfterSeconds(err: unknown): number | null {
  if (!isApiError(err)) return null;

  const header = readHeader(err, 'retry-after')?.trim();
  if (header) {
    if (/^\d+$/.test(header)) return Math.max(0, Number(header));
    const at = Date.parse(header);
    if (!Number.isNaN(at)) return Math.max(0, Math.ceil((at - Date.now()) / 1000));
  }

  const text = detailToMessage(getErrorDetail(err)) ?? err.message ?? '';
  const match = RETRY_TEXT.exec(text);
  if (match) {
    const amount = Number(match[1]);
    const unit = match[2].toLowerCase();
    return unit.startsWith('m') ? amount * 60 : amount;
  }
  return null;
}
