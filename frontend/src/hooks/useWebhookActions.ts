/**
 * Shared hook for webhook CRUD + test operations.
 * Used by both Setup wizard NotificationsStep and Settings webhooks tab.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import toast from 'react-hot-toast';
import type { Webhook } from '@/types/api';

export type { Webhook };

export function useWebhookActions() {
  const queryClient = useQueryClient();

  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null);

  const { data: webhooks = [], refetch } = useQuery<Webhook[]>({
    queryKey: ['webhooks'],
    queryFn: () => api.get('/webhooks'),
  });

  const addWebhook = useMutation({
    mutationFn: () => api.post('/webhooks', { name: name.trim(), url: url.trim(), enabled: true }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      toast.success('Webhook added');
      setName('');
      setUrl('');
      setTestResult(null);
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to add webhook');
    },
  });

  const deleteWebhook = useMutation({
    mutationFn: (id: number) => api.delete(`/webhooks/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      toast.success('Webhook removed');
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to remove webhook');
    },
  });

  const testWebhookById = useMutation({
    mutationFn: (id: number) => api.post(`/webhooks/${id}/test`),
    onSuccess: () => toast.success('Test notification sent!'),
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to send test notification');
    },
  });

  const testWebhookUrl = useMutation({
    mutationFn: () => api.post('/webhooks/test-url', { url: url.trim() }),
    onSuccess: () => {
      setTestResult({ ok: true });
      toast.success('Test notification sent!');
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      const errorMsg = axErr?.response?.data?.detail || 'Failed to send test notification';
      setTestResult({ ok: false, error: errorMsg });
      toast.error(errorMsg);
    },
  });

  const toggleWebhook = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.put(`/webhooks/${id}`, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['webhooks'] }),
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to update webhook');
    },
  });

  return {
    webhooks,
    refetch,
    name, setName,
    url, setUrl,
    testResult, setTestResult,
    addWebhook,
    deleteWebhook,
    testWebhookById,
    testWebhookUrl,
    toggleWebhook,
  };
}
