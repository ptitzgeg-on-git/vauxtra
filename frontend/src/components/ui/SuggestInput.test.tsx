/**
 * The field that replaced `<input list>`: it offers what is known and never overrides what
 * was typed. Enter keeps the typed value unless a suggestion was highlighted on purpose.
 */
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SuggestInput } from './SuggestInput';

function Domain({ onValue = vi.fn() }: { onValue?: (v: string) => void }) {
  const [value, setValue] = useState('');
  return (
    <SuggestInput
      aria-label="Domain"
      suggestions={['example.com', 'home.example.com', 'lab.test']}
      value={value}
      onValueChange={(v) => {
        setValue(v);
        onValue(v);
      }}
    />
  );
}

const field = () => screen.getByRole('combobox', { name: 'Domain' });
const shown = () => screen.queryAllByRole('option').map((o) => o.textContent);

describe('SuggestInput', () => {
  it('offers every known value on focus, then only those matching what is typed', async () => {
    render(<Domain />);
    await userEvent.click(field());
    expect(shown()).toEqual(['example.com', 'home.example.com', 'lab.test']);

    await userEvent.type(field(), 'lab');
    expect(shown()).toEqual(['lab.test']);
  });

  it('picks a suggestion with the arrows and Enter', async () => {
    const onValue = vi.fn();
    render(<Domain onValue={onValue} />);
    await userEvent.click(field());
    await userEvent.keyboard('{ArrowDown}{ArrowDown}{Enter}');
    expect(onValue).toHaveBeenLastCalledWith('home.example.com');
    expect(field()).toHaveValue('home.example.com');
  });

  it('keeps a typed value that is not in the list', async () => {
    render(<Domain />);
    await userEvent.type(field(), 'other.org{Enter}');
    expect(field()).toHaveValue('other.org');
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
