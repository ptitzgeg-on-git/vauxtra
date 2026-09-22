import { describe, expect, it } from 'vitest';
import { breakableSpaces, maskedIndexes } from './locale-typography.mjs';

/*
 * Measured on fr.json the day this landed: not one French value reaches the mask or the
 * clock rule -- every site that could hold a breakable space already holds a no-break one.
 * The mask has no subject today. It exists for the sentence written next month, which will
 * carry a port, a URL or a placeholder, and which must not be refused for it. That is
 * exactly the kind of code that rots unseen, so it is pinned here rather than trusted.
 *
 * Read `breakableSpaces` as: the punctuation marks whose space is WRONG. An empty array is
 * a value the guard accepts.
 */

const NNBSP = '\u202f'; // U+202F, before ? ! ;
const NBSP = '\u00a0'; // U+00A0, before : and inside the guillemets
const OPEN = '\u00ab'; // «
const CLOSE = '\u00bb'; // »

describe('breakableSpaces: what it must take', () => {
  it('reports an ordinary space before a question mark', () => {
    expect(breakableSpaces('Voulez-vous continuer ?')).toEqual(['?']);
  });

  it('reports an ordinary space before a colon', () => {
    expect(breakableSpaces('Resultat : 3 services')).toEqual([':']);
  });

  it('reports both guillemets, which frame their content', () => {
    expect(breakableSpaces(OPEN + ' test ' + CLOSE)).toEqual([OPEN, CLOSE]);
  });

  it('does not let a URL elsewhere in the line excuse real punctuation', () => {
    expect(breakableSpaces('Ouvrir https://exemple.test/a : voir')).toEqual([':']);
  });

  it('does not let a path glued to the word before excuse it either', () => {
    expect(breakableSpaces('Voir exemple.test/a ?')).toEqual(['?']);
  });
});

describe('breakableSpaces: what it must refuse', () => {
  it('takes a narrow no-break space before ? ! ;', () => {
    expect(breakableSpaces('Continuer' + NNBSP + '? Vraiment' + NNBSP + '! Oui' + NNBSP + ';')).toEqual([]);
  });

  it('takes a no-break space before a colon', () => {
    expect(breakableSpaces('Resultat' + NBSP + ': 3 services')).toEqual([]);
  });

  it('takes no-break spaces inside the guillemets', () => {
    expect(breakableSpaces(OPEN + NBSP + 'test' + NBSP + CLOSE)).toEqual([]);
  });

  it('leaves a port alone: the colon belongs to the address, not to French', () => {
    expect(breakableSpaces('Le port est :8443')).toEqual([]);
  });

  it('leaves punctuation inside a placeholder alone', () => {
    expect(breakableSpaces('{n, select, other {Continuer ?}}')).toEqual([]);
  });
});

describe('maskedIndexes: what it covers', () => {
  it('covers a placeholder and nothing around it', () => {
    const value = 'a {x} b';
    expect([...maskedIndexes(value)].sort((p, q) => p - q)).toEqual([2, 3, 4]);
  });

  it('covers a technical run and leaves the prose around it readable', () => {
    const value = 'voir https://h/p ici';
    const mask = maskedIndexes(value);
    expect(mask.has(value.indexOf('https'))).toBe(true);
    expect(mask.has(value.indexOf('voir'))).toBe(false);
    expect(mask.has(value.indexOf('ici'))).toBe(false);
  });
});
