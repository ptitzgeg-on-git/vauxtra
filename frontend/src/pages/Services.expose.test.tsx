/**
 * The routes page opens the expose form from the address bar and from the keyboard. Until
 * `exposeModalHarness` the form was only ever mounted in edit mode, by its own tests, so the
 * four ways in were not covered (ux-audit.md § 2.2, "Page").
 */
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import {
  callsTo,
  field,
  installExposeApi,
  savedService,
  world,
} from '@/components/features/expose/exposeModalHarness';
import { initialForm } from '@/components/features/expose/types';
import { renderWithProviders } from '@/test/render';
import { Services } from './Services';

installExposeApi();

describe('the routes page opens the expose form', () => {
  it('opens an empty form on ?new=1', async () => {
    renderWithProviders(<Services />, { route: '/services?new=1' });
    const dialog = await screen.findByRole('dialog', { name: 'expose.title.create' });
    expect(within(dialog).getByText('expose.description.create')).toBeInTheDocument();
    expect(field.subdomain()).toHaveValue(initialForm.subdomain);
    expect(field.target()).toHaveValue('');
  });

  it('opens the template on ?template=', async () => {
    world.templateApply = {
      _template_id: 5,
      _template_name: 'LAN media',
      expose_mode: 'proxy_dns',
      proxy_provider_id: 7,
      dns_provider_id: 3,
      domain: 'example.com',
      target_port: 8096,
    };
    renderWithProviders(<Services />, { route: '/services?template=5' });
    const dialog = await screen.findByRole('dialog', { name: 'expose.title.create' });
    expect(callsTo('get', '/templates/5/apply')).toHaveLength(1);
    expect(within(dialog).getByText('expose.from_template')).toBeInTheDocument();
    expect(field.domain()).toHaveValue('example.com');
    expect(field.port()).toHaveValue('8096');
  });

  it('opens the service on ?edit=', async () => {
    world.services = [savedService()];
    renderWithProviders(<Services />, { route: '/services?edit=42' });
    await screen.findByRole('dialog', { name: 'expose.title.edit' });
    expect(field.subdomain()).toHaveValue('grafana');
    expect(field.target()).toHaveValue('10.0.0.20');
  });

  it('opens the create form on "n"', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Services />, { route: '/services' });
    await screen.findByRole('combobox', { name: 'services.filter.tag' });
    expect(screen.queryByRole('dialog')).toBeNull();
    await user.keyboard('n');
    expect(await screen.findByRole('dialog', { name: 'expose.title.create' })).toBeInTheDocument();
  });
});
