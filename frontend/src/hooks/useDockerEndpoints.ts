/**
 * Shared hook for Docker endpoint CRUD operations.
 * Used by both Setup wizard DockerStep and ProviderModal Docker form.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import toast from 'react-hot-toast';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import type { DockerEndpoint } from '@/types/api';

export type { DockerEndpoint };

export function useDockerEndpoints() {
  const queryClient = useQueryClient();
  const t = useT();

  const [name, setName] = useState('');
  const [host, setHost] = useState('unix:///var/run/docker.sock');

  // Returned whole, the way `useWebhookActions` returns its own: `endpoints = []` after a
  // failed fetch and `endpoints = []` on an instance with no Docker engine are the same
  // array, and the three screens built on this hook have to be able to tell them apart
  // before one of them draws an empty list or counts a nought.
  const endpointsQuery = useQuery<DockerEndpoint[]>({
    queryKey: ['docker-endpoints'],
    queryFn: () => api.get('/docker/endpoints'),
  });
  const endpoints = endpointsQuery.data ?? [];
  const refetch = endpointsQuery.refetch;

  const canSubmit = Boolean(name.trim()) && /^(unix|tcp|ssh):\/\//.test(host.trim());

  const addEndpoint = useMutation({
    mutationFn: () => api.post('/docker/endpoints', { name: name.trim(), docker_host: host.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['docker-endpoints'] });
      toast.success(t('settings.docker.endpoint_added'));
      setName('');
      setHost('unix:///var/run/docker.sock');
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('settings.docker.endpoint_add_failed')));
    },
  });

  const deleteEndpoint = useMutation({
    mutationFn: (id: number) => api.delete(`/docker/endpoints/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['docker-endpoints'] });
      toast.success(t('settings.docker.endpoint_removed'));
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('settings.docker.endpoint_remove_failed')));
    },
  });

  return {
    endpoints,
    endpointsQuery,
    refetch,
    name, setName,
    host, setHost,
    canSubmit,
    addEndpoint,
    deleteEndpoint,
  };
}
