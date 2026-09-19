/**
 * What Vauxtra accepts as a name, and which rule a refused name broke.
 *
 * The same rules as `app/validators.py`, returning the same codes, so the field can say what
 * is wrong while it is being typed instead of sending a name the server will refuse. Eight
 * different mistakes used to come back as one "Invalid subdomain", and the panel showed none
 * of it: a 422 fell through `translateApiError` to the caller's fallback, so an underscore
 * produced "the preflight checks could not run" in the opposite corner of the screen.
 *
 * `fqdnProblem` answers the question neither field can ask alone: each half may sit inside its
 * own limit while the name they make sits outside it.
 *
 * Two copies of one rule is the thing to be afraid of here, so `tests/test_hostname_rules.py`
 * reads this file's table and runs the shared cases through both sides.
 */

/** Every rule a subdomain can break, in the order they are tested. */
export const SUBDOMAIN_PROBLEMS = [
  'empty',
  'too_long',
  'dot_edge',
  'wildcard',
  'charset',
  'label_length',
  'hyphen_edge',
] as const;

/** Every rule a domain can break, in the order they are tested. */
export const DOMAIN_PROBLEMS = [
  'empty',
  'url',
  'wildcard',
  'too_long',
  'no_dot',
  'ip_address',
  'dot_edge',
  'charset',
  'label_length',
  'hyphen_edge',
] as const;

/** Every rule the pair can break, which is not the same question as either half. */
export const FQDN_PROBLEMS = ['too_long'] as const;

export type SubdomainProblem = (typeof SUBDOMAIN_PROBLEMS)[number];
export type DomainProblem = (typeof DOMAIN_PROBLEMS)[number];
export type FqdnProblem = (typeof FQDN_PROBLEMS)[number];

/** One DNS label: what may sit between two dots. Case is folded before this is applied. */
const LABEL_RE = /^[a-z0-9-]+$/;

/** Dotted quad, with the leading zeros Python's `ipaddress` refuses left to `looksLikeIp`. */
const IPV4_RE = /^\d{1,3}(?:\.\d{1,3}){3}$/;
/** Hex and colons: no domain label can look like this, every IPv6 literal does. */
const IPV6_RE = /^[0-9a-f:]+$/;

function labelProblem(label: string): SubdomainProblem | null {
  if (!label) return 'dot_edge';
  if (label.includes('*')) return 'wildcard';
  if (!LABEL_RE.test(label)) return 'charset';
  if (label.length > 63) return 'label_length';
  if (label.startsWith('-') || label.endsWith('-')) return 'hyphen_edge';
  return null;
}

/**
 * Whether *value* is an address rather than a name.
 *
 * Python answers this with `ipaddress.ip_address`, which has no equivalent here, so the two
 * sides can disagree on which *code* a malformed address earns -- `abc:def` is `ip_address`
 * to this function and `charset` to Python. They never disagree on the answer that matters,
 * valid or not, and that is the invariant the parity test holds.
 */
function looksLikeIp(value: string): boolean {
  if (value.includes(':')) return IPV6_RE.test(value);
  if (!IPV4_RE.test(value)) return false;
  // `01.2.3.4` is not an address: `ipaddress` refuses a padded octet, and so it is a name.
  return value.split('.').every((part) => Number(part) <= 255 && !(part.length > 1 && part.startsWith('0')));
}

/**
 * The rule *value* breaks as a subdomain, or null when it breaks none.
 *
 * Dots are allowed: `grafana.metrics` under `example.com` publishes
 * `grafana.metrics.example.com`, which every provider resolves by walking labels from the
 * right to find the zone. A wildcard has to be a whole label and the leftmost one, the only
 * position DNS gives it any meaning.
 */
export function subdomainProblem(value: string, options: { allowWildcard?: boolean } = {}): SubdomainProblem | null {
  const val = (value || '').trim().toLowerCase();
  if (!val) return 'empty';
  if (val.length > 253) return 'too_long';
  const labels = val.split('.');
  for (let index = 0; index < labels.length; index += 1) {
    const label = labels[index];
    if (label === '*') {
      if (!options.allowWildcard || index !== 0) return 'wildcard';
      continue;
    }
    const problem = labelProblem(label);
    if (problem) return problem;
  }
  return null;
}

/** A domain with its surrounding space and its trailing dot removed, the form we store. */
export function normalizeDomain(value: string): string {
  return (value || '').trim().toLowerCase().replace(/\.+$/, '');
}

/** The rule *value* breaks as a domain, or null when it breaks none. */
export function domainProblem(value: string, options: { requireDot?: boolean } = {}): DomainProblem | null {
  const val = normalizeDomain(value);
  if (!val) return 'empty';
  if (['://', '/', '@'].some((token) => val.includes(token))) return 'url';
  if (val.includes('*')) return 'wildcard';
  if (val.length > 253) return 'too_long';
  if (options.requireDot && !val.includes('.')) return 'no_dot';
  if (looksLikeIp(val)) return 'ip_address';
  for (const label of val.split('.')) {
    const problem = labelProblem(label);
    if (problem) return problem as DomainProblem;
  }
  return null;
}

/** A subdomain in the form the API stores, so the rule below measures the published name. */
export function normalizeSubdomain(value: string): string {
  return (value || '').trim().toLowerCase();
}

/**
 * The rule the name the two halves make breaks, or null when it breaks none.
 *
 * Neither field can answer this: a 250-character subdomain and a 10-character domain are
 * each acceptable on their own, and the name they make is not. Without it the route was
 * saved and every provider refused the record separately, later, in its own words.
 */
export function fqdnProblem(subdomain: string, domain: string): FqdnProblem | null {
  const joined = `${normalizeSubdomain(subdomain)}.${normalizeDomain(domain)}`.replace(/^\.+|\.+$/g, '');
  return joined.length > 253 ? 'too_long' : null;
}

/** The locale key that explains a subdomain code, e.g. `expose.validation.subdomain.charset`. */
export function subdomainProblemKey(problem: SubdomainProblem): string {
  return `expose.validation.subdomain.${problem}`;
}

/** The locale key that explains a domain code. */
export function domainProblemKey(problem: DomainProblem): string {
  return `expose.validation.domain.${problem}`;
}

/** The locale key that explains a composite code. */
export function fqdnProblemKey(problem: FqdnProblem): string {
  return `expose.validation.fqdn.${problem}`;
}
