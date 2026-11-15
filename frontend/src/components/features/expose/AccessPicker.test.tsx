/**
 * The Access step of a tunnel route, on the service form.
 *
 * What it must never do is offer an empty list where it knows nothing: a token that cannot
 * read Access, and a read that failed, both look like "this account has no policies" if the
 * picker only draws what it was given. Nor offer a second application for a hostname Access
 * already covers, which Cloudflare refuses for a protected route and which would stack on a
 * hand-written setup for a partly protected one.
 *
 * `renderWithProviders` leaves `I18nProvider` out on purpose, so `t()` returns the key.
 */

import { useState } from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import type { AccessHost, TunnelAccessItem, TunnelAccessResponse } from '@/types/api';

const HOST = 'grafana.example.test';

const openHost = (state: AccessHost['state'] = 'open'): AccessHost => ({
  hostname: HOST,
  state,
  reason: state === 'open' ? 'no_application' : '',
  managed: true,
  observed: '',
  apps: state === 'open' ? [] : [{ id: 'app-1', name: 'Grafana staff' }],
  policies: [],
});

const ITEM: TunnelAccessItem = {
  id: 9,
  name: 'cf-tunnel',
  permission: 'ok',
  reason: '',
  read_permission: 'Access: Apps and Policies Read',
  edit_permission: 'Access: Apps and Policies Edit',
  application_count: 1,
  policies: [
    { id: 'pol-staff', name: 'Staff', decision: 'allow', everyone: false, app_count: 2 },
    { id: 'pol-all', name: 'Anyone', decision: 'allow', everyone: true, app_count: 0 },
  ],
  counts: { protected: 0, partial: 0, open: 1, unknown: 0 },
  hosts: [openHost()],
};

// A `vi.fn(impl)` factory keeps its implementation across `restoreMocks`, so the answer
// lives in a variable each test sets rather than in per-test `mockImplementation`.
let answer: TunnelAccessResponse | Error = { total: 1, items: [ITEM] };

vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((path: string) => {
      if (path !== '/providers/tunnels/access') return Promise.resolve([]);
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    }),
    post: vi.fn(() => Promise.resolve({ ok: true })),
  },
}));

const { AccessPicker } = await import('./AccessPicker');
const { api } = await import('@/api/client');

/** The picker as the form holds it: the selection lives in the parent. */
function Harness({ providerId = '9', onChange }: { providerId?: string; onChange?: (ids: string[]) => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  return (
    <AccessPicker
      providerId={providerId}
      hostname={HOST.toUpperCase()}
      selected={selected}
      onChange={(ids) => {
        setSelected(ids);
        onChange?.(ids);
      }}
    />
  );
}

const withItem = (patch: Partial<TunnelAccessItem>) => {
  answer = { total: 1, items: [{ ...ITEM, ...patch }] };
};

beforeEach(() => {
  answer = { total: 1, items: [ITEM] };
});

describe('AccessPicker', () => {
  it("offers the tunnel account's reusable policies, and keeps the ones ticked", async () => {
    const onChange = vi.fn();
    renderWithProviders(<Harness onChange={onChange} />);

    const staff = await screen.findByRole('checkbox', { name: /Staff/ });
    expect(screen.getByRole('checkbox', { name: /Anyone/ })).not.toBeNull();
    // A policy that lets everyone in is offered, and says so.
    expect(screen.getByText('monitoring.access.modal.everyone')).not.toBeNull();
    expect(screen.getByText('expose.access.none_checked')).not.toBeNull();

    await userEvent.click(staff);
    expect(onChange).toHaveBeenLastCalledWith(['pol-staff']);
    // Once something is ticked, the hint names what the save will need from the token.
    expect(screen.getByText('expose.access.edit_needed')).not.toBeNull();

    await userEvent.click(staff);
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it('offers nothing for a hostname Access already protects, and points to the Access tab', async () => {
    withItem({ hosts: [openHost('protected')] });
    renderWithProviders(<Harness />);

    expect(await screen.findByText(/expose\.access\.already/)).not.toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.getByRole('link', { name: 'expose.access.open_tab' }).getAttribute('href')).toBe(
      '/monitoring?tab=access',
    );
  });

  it('treats a partly protected hostname the same way', async () => {
    withItem({ hosts: [openHost('partial')] });
    renderWithProviders(<Harness />);

    expect(await screen.findByText(/expose\.access\.already/)).not.toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('says the token cannot read Access rather than drawing an empty list', async () => {
    withItem({ permission: 'missing', policies: [] });
    renderWithProviders(<Harness />);

    expect(await screen.findByText('expose.access.cannot_read')).not.toBeNull();
    expect(screen.queryByText('monitoring.access.modal.no_policies')).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('says Access could not be read when the read fails', async () => {
    answer = new Error('cloudflare unreachable');
    renderWithProviders(<Harness />);

    expect(await screen.findByText('expose.access.unread')).not.toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('says so for a tunnel the read does not describe', async () => {
    renderWithProviders(<Harness providerId="12" />);

    expect(await screen.findByText('expose.access.unread')).not.toBeNull();
  });

  it('sends the account to Cloudflare when it has no reusable policy', async () => {
    withItem({ policies: [] });
    renderWithProviders(<Harness />);

    expect(await screen.findByText('monitoring.access.modal.no_policies')).not.toBeNull();
  });

  it('writes nothing itself', async () => {
    renderWithProviders(<Harness />);
    await userEvent.click(await screen.findByRole('checkbox', { name: /Staff/ }));

    expect(api.post).not.toHaveBeenCalled();
  });
});
