/**
 * The panel's half of a rule the server also carries.
 *
 * `hostname.ts` exists so the subdomain field can say what is wrong while it is being typed;
 * `app/validators.py` decides what the API actually accepts. Two copies of one rule is the
 * arrangement where one of them quietly stops agreeing, so neither file owns it:
 * `hostname.cases.json` does, and `tests/test_hostname_rules.py` runs the same table through
 * Python. The two run in different CI jobs, so changing one side alone turns the other red.
 *
 * The one licensed difference is which *code* a malformed address earns -- Python asks
 * `ipaddress.ip_address`, this file reads the shape -- and `domain_verdict_only` holds those
 * values with only the verdict asserted, which is the answer an operator ever sees.
 */

import { describe, expect, it } from 'vitest';
import cases from './hostname.cases.json';
import {
  DOMAIN_PROBLEMS,
  SUBDOMAIN_PROBLEMS,
  domainProblem,
  domainProblemKey,
  subdomainProblem,
  subdomainProblemKey,
  normalizeDomain,
} from './hostname';

describe('subdomainProblem', () => {
  for (const c of cases.subdomain) {
    it(`${JSON.stringify(c.value)}: ${c.why}`, () => {
      expect(subdomainProblem(c.value, { allowWildcard: c.allow_wildcard })).toBe(c.problem);
    });
  }

  it('reaches every code the module declares', () => {
    // A table that never produces `hyphen_edge` would let `hyphen_edge` rot unnoticed.
    const reached = new Set(cases.subdomain.map((c) => c.problem).filter(Boolean));
    expect([...reached].sort()).toEqual([...SUBDOMAIN_PROBLEMS].sort());
  });

  it('accepts something', () => {
    // The negative control: a table of only refusals would pass a function that refuses all.
    expect(cases.subdomain.filter((c) => c.problem === null).length).toBeGreaterThanOrEqual(5);
  });
});

describe('domainProblem', () => {
  for (const c of cases.domain) {
    it(`${JSON.stringify(c.value)}: ${c.why}`, () => {
      expect(domainProblem(c.value, { requireDot: c.require_dot })).toBe(c.problem);
    });
  }

  for (const c of cases.domain_verdict_only) {
    it(`${JSON.stringify(c.value)}: ${c.why}`, () => {
      expect(domainProblem(c.value, { requireDot: c.require_dot }) === null).toBe(c.valid);
    });
  }

  it('reaches every code the module declares', () => {
    const reached = new Set(cases.domain.map((c) => c.problem).filter(Boolean));
    expect([...reached].sort()).toEqual([...DOMAIN_PROBLEMS].sort());
  });

  it('accepts something', () => {
    expect(cases.domain.filter((c) => c.problem === null).length).toBeGreaterThanOrEqual(5);
  });
});

describe('normalizeDomain', () => {
  it('drops the trailing dot of an absolute name and folds case', () => {
    // The form stores what this returns, so `EXAMPLE.COM.` and `example.com` are one domain.
    expect(normalizeDomain('  EXAMPLE.COM.  ')).toBe('example.com');
  });
});

describe('the locale keys', () => {
  it('name the block the translation files carry', () => {
    // These strings are built, never written literally, so the scan in
    // `tests/test_frontend_i18n.py` cannot see them; `test_hostname_rules.py` checks they
    // exist in all eight files. This holds the shape those two agree on.
    expect(subdomainProblemKey('charset')).toBe('expose.validation.subdomain.charset');
    expect(domainProblemKey('ip_address')).toBe('expose.validation.domain.ip_address');
  });
});
