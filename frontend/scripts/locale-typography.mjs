/**
 * The French two-part punctuation rule, alone in its own file.
 *
 * `check-locale-quality.mjs` enforces it and this module decides it, which is what lets the
 * decision be tested on its own. A guard that fails tells an author a sentence is wrong; it
 * cannot tell them the guard itself still reads a port number correctly, and the sentence
 * that proves it does is the one nobody has written yet. So the rule is exported, and
 * `locale-typography.test.mjs` holds it to twelve cases the locale files do not contain.
 */

/**
 * French puts a no-break space before its two-part punctuation, and the browser is the
 * reason: a plain space there lets the `?` wrap onto a line of its own, under the sentence
 * it belongs to. U+202F before ? ! ; and U+00A0 before : and inside the guillemets.
 *
 * What keeps this off `{count}`, `https://host/path`, `host:8443` and `12:30` is a MASK,
 * not a cleverer regular expression: those all carry a colon that is not French
 * punctuation, and marking their character positions is the only rule that stays readable
 * a year from now. A site is refused when the character that DECIDES it -- the punctuation
 * itself, or the guillemet the space belongs to -- falls inside a masked run.
 */
const TYPO_PLACEHOLDER = /\{[^}]*\}/g;
const TYPO_RUN = /\S+/g;
const TYPO_TECHNICAL = /:\/\/|@|\/|\\|^\d+:\d+$|^[\w.-]+:\d+$/;

function maskedIndexes(value) {
  const mask = new Set();
  for (const m of value.matchAll(TYPO_PLACEHOLDER)) {
    for (let i = m.index; i < m.index + m[0].length; i += 1) mask.add(i);
  }
  for (const m of value.matchAll(TYPO_RUN)) {
    if (!TYPO_TECHNICAL.test(m[0])) continue;
    for (let i = m.index; i < m.index + m[0].length; i += 1) mask.add(i);
  }
  return mask;
}

function breakableSpaces(value) {
  const mask = maskedIndexes(value);
  const found = [];

  for (let i = 0; i < value.length; i += 1) {
    if (value[i] !== ' ') continue;
    const next = value[i + 1] ?? '';
    let anchor;
    let shown;

    if (next === '?' || next === '!' || next === ';' || next === ':' || next === '»') {
      [anchor, shown] = [i + 1, next];
    } else if (value[i - 1] === '«') {
      [anchor, shown] = [i - 1, '«'];
    } else {
      continue;
    }

    if (mask.has(i) || mask.has(anchor)) continue;
    // A colon glued to a digit is a port or a clock, never a French label: ` :8080`, ` :30`.
    if (next === ':' && /\d/.test(value[i + 2] ?? '')) continue;
    found.push(shown);
  }

  return [...new Set(found)];
}

export { breakableSpaces, maskedIndexes };
