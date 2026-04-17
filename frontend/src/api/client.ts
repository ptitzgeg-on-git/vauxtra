import axios from 'axios';

const _axios = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

_axios.interceptors.response.use(
  (response) => response.data,
  (error: unknown) => {
    const axiosError = axios.isAxiosError(error) ? error : null;
    // On 401, invalidate auth cache so AuthGate shows login
    if (axiosError?.response?.status === 401 && !axiosError.config?.url?.includes('/auth/')) {
      window.dispatchEvent(new CustomEvent('vauxtra:auth-expired'));
    }
    if (import.meta.env.DEV) {
      console.error('API Error:', axiosError?.response?.data ?? error);
    }
    return Promise.reject(error);
  },
);

// Re-export with correct return types: the interceptor unwraps response.data,
// so callers receive T directly instead of AxiosResponse<T>.
export const api = {
  get<T = unknown>(url: string, config?: Parameters<typeof _axios.get>[1]): Promise<T> {
    return _axios.get<T>(url, config) as unknown as Promise<T>;
  },
  post<T = unknown>(url: string, data?: unknown, config?: Parameters<typeof _axios.post>[2]): Promise<T> {
    return _axios.post<T>(url, data, config) as unknown as Promise<T>;
  },
  put<T = unknown>(url: string, data?: unknown, config?: Parameters<typeof _axios.put>[2]): Promise<T> {
    return _axios.put<T>(url, data, config) as unknown as Promise<T>;
  },
  delete<T = unknown>(url: string, config?: Parameters<typeof _axios.delete>[1]): Promise<T> {
    return _axios.delete<T>(url, config) as unknown as Promise<T>;
  },
};
