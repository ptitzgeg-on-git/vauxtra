/**
 * The success sentence names the host exactly once.
 *
 * It used to name it twice: the screen called `t('expose.done.body')` with no parameter, so
 * the paragraph printed the literal `{host}` and the value was appended after it. Handing
 * `t()` the host instead would have fixed the count and lost the monospace run, because
 * `t()` returns a plain string. Splitting the template on its own placeholder is what keeps
 * both, and these are the cases that splitting has to survive.
 */

import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import { PublishedSentence } from './ExposeModal';

const HOST = 'app.xeno.homes';

/** The English string as it stands in `en.json`, placeholder included. */
const EN = '{host} is published on its providers.';

describe('PublishedSentence', () => {
  it('prints the host once and leaves no placeholder behind', () => {
    const { container } = renderWithProviders(<PublishedSentence template={EN} host={HOST} />);

    expect(container.textContent).toBe('app.xeno.homes is published on its providers.');
    expect(container.textContent).not.toContain('{host}');
    expect(screen.getAllByText(HOST)).toHaveLength(1);
  });

  it('gives the host its own monospace run rather than the whole sentence', () => {
    const { container } = renderWithProviders(<PublishedSentence template={EN} host={HOST} />);

    const mono = container.querySelectorAll('.font-mono');
    expect(mono).toHaveLength(1);
    expect(mono[0]).toHaveTextContent(HOST);
    expect(mono[0]?.textContent).toBe(HOST);
  });

  it('keeps the words in the order the translation put them', () => {
    // All eight sentences open on the host today, and none of them has to: a translator is
    // free to move it, and the sentence has to read correctly when they do.
    const { container } = renderWithProviders(
      <PublishedSentence template="Der Dienst {host} ist veroeffentlicht." host={HOST} />,
    );

    expect(container.textContent).toBe('Der Dienst app.xeno.homes ist veroeffentlicht.');
  });

  it('still names the host when a translation dropped the placeholder', () => {
    const { container } = renderWithProviders(
      <PublishedSentence template="Der Dienst ist veroeffentlicht." host={HOST} />,
    );

    expect(container.textContent).toBe('Der Dienst ist veroeffentlicht.app.xeno.homes');
    expect(screen.getAllByText(HOST)).toHaveLength(1);
  });
});
