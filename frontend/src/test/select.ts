/**
 * Reading a `Select` from a test.
 *
 * The list is drawn by the app, not by the platform, so there are no `<option>` elements to
 * read. A closed select keeps its list in the page, hidden, and `aria-controls` names it:
 * these helpers read that, without opening anything. That matters inside `waitFor`, which
 * re-runs its callback on every DOM mutation -- a helper that opened and closed the list
 * would feed its own loop and never let the awaited data arrive.
 */
import { fireEvent } from '@testing-library/react';

const SELECT_TRIGGERS = '[role="combobox"][aria-haspopup="listbox"]';

function readOptions(combobox: HTMLElement): HTMLElement[] {
  const listId = combobox.getAttribute('aria-controls');
  const list = listId ? document.getElementById(listId) : null;
  return list ? Array.from(list.querySelectorAll<HTMLElement>('[role="option"]')) : [];
}

/** The option labels of one select, in order. */
export function optionLabels(combobox: HTMLElement): string[] {
  return readOptions(combobox).map((option) => (option.textContent ?? '').trim());
}

/** The option labels of every select under `root`. */
export function allOptionLabels(root: ParentNode = document.body): string[] {
  return Array.from(root.querySelectorAll<HTMLElement>(SELECT_TRIGGERS)).flatMap(optionLabels);
}

/** The value a select holds -- what `select.value` was on the native element. */
export function selectValue(combobox: HTMLElement): string {
  return combobox.getAttribute('data-value') ?? '';
}

/** Opens `combobox` and returns its option called `name`, left open for the caller to click. */
export function openOption(combobox: HTMLElement, name: string): HTMLElement {
  if (combobox.getAttribute('aria-expanded') !== 'true') fireEvent.click(combobox);
  const match = readOptions(combobox).find((option) => (option.textContent ?? '').trim() === name);
  if (!match) throw new Error(`No option "${name}" in this select`);
  return match;
}
