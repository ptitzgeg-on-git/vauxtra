/**
 * What `@/api/client` resolves to under Vitest. See `vitest.config.ts` for the alias.
 *
 * Every method answers with an empty value instead of a request. Nothing here is meant to
 * stand in for the backend: a component that needs data takes it as a prop, or the test
 * seeds it with `setQueryData`. The one caller that reaches this in practice is
 * `useFormat`'s `/settings` query, for which an empty object is the correct answer anyway --
 * it means "follow the browser's time zone".
 */

export const API_BASE_URL = '/api';

export const api = {
  get: async <T,>(): Promise<T> => ({}) as T,
  post: async <T,>(): Promise<T> => ({}) as T,
  put: async <T,>(): Promise<T> => ({}) as T,
  patch: async <T,>(): Promise<T> => ({}) as T,
  delete: async <T,>(): Promise<T> => ({}) as T,
};
