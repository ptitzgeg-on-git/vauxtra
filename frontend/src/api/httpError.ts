// Its own module, not part of `./client`: tests replace the client with `vi.mock`, and the
// error readers in `@/lib/errors` still need the real class to recognise a failed call.

export interface HttpErrorResponse {
  status: number;
  data: unknown;
  headers: Record<string, string>;
}

/**
 * A call that failed. `response` is there when the server answered with a non-2xx status,
 * and absent when no answer came back at all (offline, DNS, refused). An aborted call rejects
 * with the browser's own `AbortError` instead.
 */
export class HttpError extends Error {
  readonly url: string;
  readonly response?: HttpErrorResponse;

  constructor(message: string, url: string, response?: HttpErrorResponse) {
    super(message);
    this.name = 'HttpError';
    this.url = url;
    this.response = response;
  }
}
