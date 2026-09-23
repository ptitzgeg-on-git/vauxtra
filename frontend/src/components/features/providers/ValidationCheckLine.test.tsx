/**
 * One validation line, drawn the same way in the Integrations modal and the setup wizard.
 *
 * Both lists drew every check that was not `ok` as a red cross, so "no hostname was given"
 * and "the token was refused" looked alike, and a non-blocking line sat in red under a green
 * "Connection validated".
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ProviderValidationCheck } from '@/types/api';

import { ValidationCheckLine } from './ValidationCheckLine';

function drawn(check: ProviderValidationCheck): HTMLElement {
  render(
    <ul>
      <ValidationCheckLine check={check} />
    </ul>,
  );
  return screen.getByRole('listitem');
}

describe('a validation line', () => {
  it('is green when the check passed', () => {
    const line = drawn({ name: 'token_verify', ok: true, blocking: true, detail: 'API token is active' });
    expect(line).toHaveAttribute('data-tone', 'success');
    expect(line).toHaveClass('text-success');
  });

  it('is grey when the check was not run', () => {
    const line = drawn({ name: 'tunnel_config_write', ok: false, blocking: false, skipped: true, detail: 'Write probe skipped (safe mode).' });
    expect(line).toHaveAttribute('data-tone', 'skipped');
    expect(line).toHaveClass('text-muted-foreground');
  });

  it('is amber when the check failed without blocking anything', () => {
    const line = drawn({ name: 'zone_lookup', ok: false, blocking: false, detail: 'Cannot resolve DNS zone for hostname' });
    expect(line).toHaveAttribute('data-tone', 'warning');
    expect(line).toHaveClass('text-warning');
  });

  it('is red when the check failed and blocks', () => {
    const line = drawn({ name: 'token_verify', ok: false, blocking: true, detail: 'Token verification failed' });
    expect(line).toHaveAttribute('data-tone', 'danger');
    expect(line).toHaveClass('text-destructive');
  });

  it('prints the sentence the API sent when this build does not know its code', () => {
    drawn({ name: 'dns_read', ok: true, blocking: false, detail: 'Can read zone DNS records', detail_code: 'not_in_this_build' });
    expect(screen.getByText('Can read zone DNS records')).toBeInTheDocument();
  });
});
