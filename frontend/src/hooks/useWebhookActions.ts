/**
 * Shared hook for webhook CRUD + test operations.
 * Used by both Setup wizard NotificationsStep and Settings webhooks tab.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import toast from 'react-hot-toast';
import type { Webhook } from '@/types/api';
import { useI18n } from '@/i18n';

export type { Webhook };

export function useWebhookActions() {
  const queryClient = useQueryClient();
  const { t } = useI18n();

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
      toast.success(t('settings.webhooks.added'));
      setName('');
      setUrl('');
      setTestResult(null);
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || t('settings.webhooks.add_failed'));
    },
  });

  const deleteWebhook = useMutation({
    mutationFn: (id: number) => api.delete(`/webhooks/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      toast.success(t('settings.webhooks.removed'));
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || t('settings.webhooks.remove_failed'));
    },
  });

  const testWebhookById = useMutation({
    mutationFn: (id: number) => api.post(`/webhooks/${id}/test`),
    onSuccess: () => toast.success(t('settings.webhooks.test_sent')),
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || t('settings.webhooks.test_failed'));
    },
  });

  const testWebhookUrl = useMutation({
    mutationFn: () => api.post('/webhooks/test-url', { url: url.trim() }),
    onSuccess: () => {
      setTestResult({ ok: true });
      toast.success(t('settings.webhooks.test_sent'));
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      const errorMsg = axErr?.response?.data?.detail || t('settings.webhooks.test_failed');
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
      toast.error(axErr?.response?.data?.detail || t('settings.webhooks.update_failed'));
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
