/**
 * Shared hook for Docker endpoint CRUD operations.
 * Used by both Setup wizard DockerStep and ProviderModal Docker form.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import toast from 'react-hot-toast';
import type { DockerEndpoint } from '@/types/api';

export type { DockerEndpoint };

export function useDockerEndpoints() {
  const queryClient = useQueryClient();

  const [name, setName] = useState('');
  const [host, setHost] = useState('unix:///var/run/docker.sock');

  const { data: endpoints = [], refetch } = useQuery<DockerEndpoint[]>({
    queryKey: ['docker-endpoints'],
    queryFn: () => api.get('/docker/endpoints'),
  });

  const canSubmit = Boolean(name.trim()) && /^(unix|tcp|ssh):\/\//.test(host.trim());

  const addEndpoint = useMutation({
    mutationFn: () => api.post('/docker/endpoints', { name: name.trim(), docker_host: host.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['docker-endpoints'] });
      toast.success('Docker endpoint added');
      setName('');
      setHost('unix:///var/run/docker.sock');
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to add Docker endpoint');
    },
  });

  const deleteEndpoint = useMutation({
    mutationFn: (id: number) => api.delete(`/docker/endpoints/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['docker-endpoints'] });
      toast.success('Docker endpoint removed');
    },
    onError: (err: unknown) => {
      const axErr = err as { response?: { data?: { detail?: string } } };
      toast.error(axErr?.response?.data?.detail || 'Failed to remove endpoint');
    },
  });

  return {
    endpoints,
    refetch,
    name, setName,
    host, setHost,
    canSubmit,
    addEndpoint,
    deleteEndpoint,
  };
}
