/**
 * The select drawn by the app. What the native element gave for free and this one has to
 * keep: the value/onChange contract every page is written against, the keyboard, disabled
 * options that stay unpickable, and an Escape that closes the list without closing the
 * dialog the list was opened from.
 */
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal } from './Modal';
import { Select } from './Select';

function Fruit({ onChange = vi.fn(), initial = 'pear' }: { onChange?: (value: string) => void; initial?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <Select
      aria-label="Fruit"
      value={value}
      onChange={(e) => {
        setValue(e.target.value);
        onChange(e.target.value);
      }}
    >
      <option value="apple">Apple</option>
      <option value="banana" disabled>
        Banana
      </option>
      <option value="cherry">Cherry</option>
      <option value="pear">Pear</option>
    </Select>
  );
}

const trigger = () => screen.getByRole('combobox', { name: 'Fruit' });

describe('Select', () => {
  it('shows the chosen option and keeps its list shut until asked', () => {
    render(<Fruit />);
    expect(trigger()).toHaveTextContent('Pear');
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('opens on click, marks the current option, and reports a pick through onChange', async () => {
    const onChange = vi.fn();
    render(<Fruit onChange={onChange} />);
    await userEvent.click(trigger());

    const list = screen.getByRole('listbox');
    expect(list).toBeVisible();
    expect(screen.getByRole('option', { name: 'Pear' })).toHaveAttribute('aria-selected', 'true');

    await userEvent.click(screen.getByRole('option', { name: 'Cherry' }));
    expect(onChange).toHaveBeenCalledWith('cherry');
    expect(trigger()).toHaveTextContent('Cherry');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('never picks a disabled option, by pointer or by keyboard', async () => {
    const onChange = vi.fn();
    render(<Fruit onChange={onChange} initial="apple" />);
    await userEvent.click(trigger());
    await userEvent.click(screen.getByRole('option', { name: 'Banana' }));
    expect(onChange).not.toHaveBeenCalled();

    // From Apple, ArrowDown steps over Banana to Cherry.
    await userEvent.keyboard('{ArrowDown}{Enter}');
    expect(onChange).toHaveBeenCalledWith('cherry');
  });

  it('walks and picks with the keyboard alone', async () => {
    const onChange = vi.fn();
    render(<Fruit onChange={onChange} />);
    trigger().focus();

    await userEvent.keyboard('{ArrowDown}');
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    await userEvent.keyboard('{Home}{Enter}');
    expect(onChange).toHaveBeenLastCalledWith('apple');
    expect(trigger()).toHaveFocus();
  });

  it('jumps to what is typed', async () => {
    const onChange = vi.fn();
    render(<Fruit onChange={onChange} initial="apple" />);
    trigger().focus();

    await userEvent.keyboard('c');
    await userEvent.keyboard('{Enter}');
    expect(onChange).toHaveBeenLastCalledWith('cherry');
  });

  it('shows the first option when the value matches none, as a native select does', () => {
    render(
      <Select aria-label="Tag" value="gone" onChange={() => {}}>
        <option value="">All tags</option>
        <option value="1">edge</option>
      </Select>,
    );
    expect(screen.getByRole('combobox', { name: 'Tag' })).toHaveTextContent('All tags');
  });

  it('closes the list, and only the list, on Escape inside a dialog', async () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Dialog">
        <Fruit />
      </Modal>,
    );
    await userEvent.click(trigger());
    expect(screen.getByRole('listbox')).toBeVisible();

    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();

    // With the list shut, Escape is the dialog's again.
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
