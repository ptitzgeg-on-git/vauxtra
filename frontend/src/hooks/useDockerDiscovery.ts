import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '@/api/client';
import { useT } from '@/i18n';
import { translateApiError } from '@/lib/errors';
import type { DockerEndpoint } from '@/types/api';

export type { DockerEndpoint };

export type DockerContainer = {
  id: string;
  name: string;
  image: string;
  status: string;
  target_ip: string;
  target_port: number | null;
  labels: Record<string, string>;
  suggested_subdomain: string;
  suggested_scheme: string;
  websocket: boolean;
  suggestion: {
    subdomain: string;
    target_port: number | null;
    forward_scheme: string;
    websocket: boolean;
    confidence: 'high' | 'medium' | 'low';
    source: string;
    middlewares: string[];
    tls_resolver: string | null;
  };
  endpoint_id: number | null;
  endpoint_name: string;
  existing_service?: {
    id: number;
    fqdn: string;
  } | null;
};

export function useDockerDiscovery() {
  const queryClient = useQueryClient();
  const t = useT();

  const [dockerContainers, setDockerContainers] = useState<DockerContainer[]>([]);
  const [selectedDockerIds, setSelectedDockerIds] = useState<string[]>([]);
  const [dockerDomain, setDockerDomain] = useState('');
  const [dockerProxyProviderId, setDockerProxyProviderId] = useState('');
  const [dockerDnsProviderId, setDockerDnsProviderId] = useState('');
  const [dockerDnsIp, setDockerDnsIp] = useState('');
  const [dockerEndpointId, setDockerEndpointId] = useState('');
  const [newDockerEndpointName, setNewDockerEndpointName] = useState('');
  const [newDockerEndpointHost, setNewDockerEndpointHost] = useState('');

  const { data: dockerEndpoints = [] } = useQuery<DockerEndpoint[]>({
    queryKey: ['docker-endpoints'],
    queryFn: () => api.get<DockerEndpoint[]>('/docker/endpoints'),
  });

  const { data: domains = [] } = useQuery<string[]>({
    queryKey: ['domains'],
    queryFn: () => api.get<string[]>('/domains'),
  });

  const effectiveEndpointId =
    dockerEndpointId || (dockerEndpoints[0] ? String(dockerEndpoints[0].id) : '');
  const selectedEndpoint =
    dockerEndpoints.find((ep) => String(ep.id) === effectiveEndpointId) ?? null;
  const effectiveDomain =
    dockerDomain || (domains.length > 0 ? domains[0] : '');

  const addEndpointMutation = useMutation({
    mutationFn: () =>
      api.post<DockerEndpoint>('/docker/endpoints', {
        name: newDockerEndpointName.trim(),
        docker_host: newDockerEndpointHost.trim(),
        enabled: true,
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['docker-endpoints'] });
      setDockerEndpointId(String(data?.id ?? ''));
      setNewDockerEndpointName('');
      setNewDockerEndpointHost('');
      toast.success(t('settings.docker.endpoint_added'));
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('settings.docker.endpoint_add_failed')));
    },
  });

  const testEndpointMutation = useMutation({
    mutationFn: (endpointId: string) =>
      api.post<{ containers: number }>(`/docker/endpoints/${endpointId}/test`),
    onSuccess: (data) => {
      const count = Number(data?.containers ?? 0);
      toast.success(t('settings.docker.endpoint_reachable', { count }));
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('settings.docker.endpoint_test_failed')));
    },
  });

  const setDefaultEndpointMutation = useMutation({
    mutationFn: (endpointId: string) =>
      api.post<{ ok: boolean }>(`/docker/endpoints/${endpointId}/default`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['docker-endpoints'] });
      toast.success(t('settings.docker.default_updated'));
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('settings.docker.default_update_failed')));
    },
  });

  const deleteEndpointMutation = useMutation({
    mutationFn: (endpointId: string) =>
      api.delete<{ ok: boolean }>(`/docker/endpoints/${endpointId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['docker-endpoints'] });
      setDockerEndpointId('');
      toast.success(t('settings.docker.endpoint_deleted'));
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('settings.docker.endpoint_delete_failed')));
    },
  });

  const discoverMutation = useMutation({
    mutationFn: () => {
      const qs = effectiveEndpointId
        ? `?endpoint_id=${encodeURIComponent(effectiveEndpointId)}`
        : '';
      return api.get<DockerContainer[]>(`/docker/containers${qs}`);
    },
    onSuccess: (data) => {
      setDockerContainers(data);
      setSelectedDockerIds(data.filter((c) => c.target_port !== null).map((c) => c.id));
      toast.success(t('settings.docker.toast_discovered', { count: data.length }));
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('settings.docker.discovery_failed')));
    },
  });

  const importMutation = useMutation({
    mutationFn: () => {
      if (!effectiveDomain) throw new Error(t('settings.docker.domain_required'));
      const selected = dockerContainers.filter((c) => selectedDockerIds.includes(c.id));
      return api.post<{ imported: number; skipped: number; errors: string[] }>('/docker/import', {
        endpoint_id: effectiveEndpointId ? Number(effectiveEndpointId) : null,
        domain: effectiveDomain,
        proxy_provider_id: dockerProxyProviderId ? Number(dockerProxyProviderId) : null,
        dns_provider_id: dockerDnsProviderId ? Number(dockerDnsProviderId) : null,
        dns_ip: dockerDnsIp,
        containers: selected.map((c) => ({
          id: c.id,
          name: c.name,
          subdomain: c.suggestion?.subdomain ?? c.suggested_subdomain,
          target_ip: c.target_ip,
          target_port: c.suggestion?.target_port ?? c.target_port,
          forward_scheme: c.suggestion?.forward_scheme ?? c.suggested_scheme ?? 'http',
          websocket: c.suggestion?.websocket ?? c.websocket,
        })),
      });
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['services'] });
      queryClient.invalidateQueries({ queryKey: ['health'] });
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      toast.success(
        t('settings.docker.import_done', {
          imported: data?.imported ?? 0,
          skipped: data?.skipped ?? 0,
        }),
      );
    },
    onError: (err: unknown) => {
      toast.error(translateApiError(err, t, t('settings.docker.import_failed')));
    },
  });

  return {
    // State
    dockerContainers,
    selectedDockerIds,
    setSelectedDockerIds,
    dockerEndpointId,
    setDockerEndpointId,
    dockerDomain,
    setDockerDomain,
    dockerProxyProviderId,
    setDockerProxyProviderId,
    dockerDnsProviderId,
    setDockerDnsProviderId,
    dockerDnsIp,
    setDockerDnsIp,
    newDockerEndpointName,
    setNewDockerEndpointName,
    newDockerEndpointHost,
    setNewDockerEndpointHost,
    // Derived
    dockerEndpoints,
    domains,
    effectiveEndpointId,
    selectedEndpoint,
    effectiveDomain,
    // Mutations
    addEndpointMutation,
    testEndpointMutation,
    setDefaultEndpointMutation,
    deleteEndpointMutation,
    discoverMutation,
    importMutation,
  };
}
