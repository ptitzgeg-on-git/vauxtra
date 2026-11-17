/**
 * The two seeds of the form that are not the empty create form: a template
 * (`templateToFormState`) and a saved route (`toFormState`), on the points `ux-audit.md`
 * § 1.1 and § 1.4 found them wrong.
 */
import { describe, expect, it } from 'vitest';
import { savedService } from './exposeModalHarness';
import { templateToFormState, toFormState } from './types';

/** What `GET /templates/{id}/apply` answers for a route behind a proxy, on a LAN resolver. */
const lanTemplate = {
  _template_id: 5,
  _template_name: 'LAN media',
  expose_mode: 'proxy_dns',
  proxy_provider_id: 7,
  dns_provider_id: 3,
  tunnel_provider_id: null,
  domain: 'example.com',
  forward_scheme: 'https',
  websocket: true,
  target_port: 8096,
  public_target_mode: 'manual',
  dns_ip: '10.0.0.30',
  tag_ids: [1, 2],
  environment_ids: [4],
  icon_url: 'https://example.com/jellyfin.png',
};

/** The same, published in DNS alone: no proxy, so no port to forward to. */
const dnsOnlyTemplate = { ...lanTemplate, proxy_provider_id: null, target_port: null };

describe('a form seeded from a template', () => {
  // § 1.4: the doc said the subdomain stays for the user to fill, and `...initialForm` put
  // "app" back, to be erased again on every route made from the template.
  it('leaves the subdomain empty', () => {
    expect(templateToFormState(lanTemplate).subdomain).toBe('');
  });

  it('leaves the target for the user to fill', () => {
    expect(templateToFormState(lanTemplate).target_ip).toBe('');
  });

  // § 1.4: `toFormState` keeps 0, the name published in DNS alone; this made it 80.
  it('keeps port 0', () => {
    expect(templateToFormState({ ...dnsOnlyTemplate, target_port: 0 }).target_port).toBe(0);
  });

  // A template saved from a DNS-only form is posted with `target_port: null`: 1 to 65535 is
  // all the API stores. Read back as 80, the route was probed on a port it never had.
  it('reads no port as 0 on a DNS-only template', () => {
    for (const none of [null, undefined, '']) {
      expect.soft(templateToFormState({ ...dnsOnlyTemplate, target_port: none }).target_port, String(none)).toBe(0);
    }
  });

  it('reads no port as 80 behind a proxy or a tunnel', () => {
    expect(templateToFormState({ ...lanTemplate, target_port: null }).target_port).toBe(80);
    const tunnel = { ...dnsOnlyTemplate, expose_mode: 'tunnel', dns_provider_id: null, tunnel_provider_id: 9 };
    expect(templateToFormState(tunnel).target_port).toBe(80);
  });

  it('keeps the providers and labels the template names', () => {
    expect(templateToFormState(lanTemplate)).toMatchObject({
      domain: 'example.com',
      target_port: 8096,
      forward_scheme: 'https',
      websocket: true,
      expose_mode: 'proxy_dns',
      ui_expose_mode: 'dns_proxy',
      public_target_mode: 'manual',
      proxy_provider_id: '7',
      dns_provider_id: '3',
      tunnel_provider_id: '',
      dns_ip: '10.0.0.30',
      tag_ids: [1, 2],
      environment_ids: [4],
      icon_url: 'https://example.com/jellyfin.png',
    });
  });
});

describe('the tunnel hostname of a saved route', () => {
  const tunnelRoute = (tunnelHostname: string) =>
    savedService({
      expose_mode: 'tunnel',
      tunnel_provider_id: 9,
      tunnel_hostname: tunnelHostname,
      proxy_provider_id: null,
      dns_provider_id: null,
      dns_ip: '',
    });

  // § 1.1: a hostname equal to the route's own address is that address, followed. Read as
  // typed, a rename left it behind, pointing at the old name.
  it('reads a tunnel hostname equal to the address as following it', () => {
    expect(toFormState(tunnelRoute('Grafana.example.com ')).tunnel_hostname).toBe('');
  });

  // The same name written fully qualified, with the root's trailing dot, is still that name.
  it('reads one with a trailing dot as the address too', () => {
    expect(toFormState(tunnelRoute('grafana.example.com.')).tunnel_hostname).toBe('');
  });

  it('keeps a tunnel hostname of its own', () => {
    expect(toFormState(tunnelRoute('media.example.com')).tunnel_hostname).toBe('media.example.com');
  });
});
