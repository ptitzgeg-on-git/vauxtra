/**
 * The two readings of an address the expose form makes, as tables in the manner of
 * `lib/hostname.cases.json`: one row per shape, and the reason it answers what it answers.
 *
 * The IPv6 rows use 2001:db8::/32, the documentation prefix (RFC 3849), and the loopback rows
 * have to name the loopback: they are what `proxyHostTarget` exists to refuse.
 */
import { describe, expect, it } from 'vitest';
import { parseTarget, proxyHostTarget, type ParsedTarget } from './target';

const SPLIT: Array<{ value: string; parsed: ParsedTarget; why: string }> = [
  {
    value: 'https://grafana.example.com:3000/login',
    parsed: { host: 'grafana.example.com', port: 3000, scheme: 'https' },
    why: 'a whole URL, path dropped',
  },
  {
    value: 'http://grafana.example.com:3000',
    parsed: { host: 'grafana.example.com', port: 3000, scheme: 'http' },
    why: 'the URL of ux-audit.md § 1.10',
  },
  { value: 'HTTPS://Grafana.example.com', parsed: { host: 'Grafana.example.com', scheme: 'https' }, why: 'any case of scheme' },
  { value: 'http://10.0.0.20', parsed: { host: '10.0.0.20', scheme: 'http' }, why: 'no port: the form picks it' },
  { value: 'http://1', parsed: { host: '1', scheme: 'http' }, why: 'a numeric host is kept, never read as 0.0.0.1' },
  { value: 'http://grafana', parsed: { host: 'grafana', scheme: 'http' }, why: 'a single-label host' },
  { value: 'http://10.0.0.20?probe=1', parsed: { host: '10.0.0.20', scheme: 'http' }, why: 'a query, dropped' },
  {
    value: 'http://[2001:db8::5]:8443/',
    parsed: { host: '2001:db8::5', port: 8443, scheme: 'http' },
    why: 'a bracketed IPv6, unbracketed',
  },
  { value: '10.0.0.20:8080', parsed: { host: '10.0.0.20', port: 8080 }, why: 'host:port, no scheme' },
  { value: 'grafana.example.com:3000', parsed: { host: 'grafana.example.com', port: 3000 }, why: 'name:port' },
  { value: '[2001:db8::5]:8080', parsed: { host: '2001:db8::5', port: 8080 }, why: '[v6]:port' },
  { value: '  http://10.0.0.20:81  ', parsed: { host: '10.0.0.20', port: 81, scheme: 'http' }, why: 'surrounding blanks' },
];

const LEFT_ALONE: Array<{ value: string; why: string }> = [
  { value: '', why: 'nothing typed' },
  { value: 'grafana.example.com', why: 'a bare name is already a target' },
  { value: '10.0.0.20', why: 'a bare address is already a target' },
  { value: '2001:db8::5', why: 'a bare IPv6: no port can be told apart from it' },
  { value: 'http://', why: 'no host yet' },
  { value: 'http://:8080', why: 'a port and no host' },
  { value: 'http://10.0.0.20:0', why: 'port 0 forwards nowhere' },
  { value: 'http://10.0.0.20:70000', why: 'past 65535' },
  { value: '10.0.0.20:0', why: 'port 0, no scheme' },
  { value: 'ftp://10.0.0.20', why: 'a scheme the route cannot forward' },
  { value: 'http://operator@10.0.0.20', why: 'a login before the host is not a target' },
  { value: 'grafana.example.com:', why: 'a colon and no port yet' },
  { value: 'http://10.0.0.20:80abc', why: 'a port that is not a number' },
];

describe('parseTarget', () => {
  it.each(SPLIT)('$value: $why', ({ value, parsed }) => {
    expect(parseTarget(value)).toEqual(parsed);
  });

  it.each(LEFT_ALONE)('leaves $value alone: $why', ({ value }) => {
    expect(parseTarget(value)).toBeNull();
  });
});

const PROXY_HOSTS: Array<{ url: string | null | undefined; host: string; why: string }> = [
  { url: 'http://10.0.0.30:81', host: '10.0.0.30', why: 'the LAN address the panel reaches the proxy on' },
  { url: 'https://npm.example.com', host: 'npm.example.com', why: 'a name is kept as a name' },
  { url: 'http://[2001:db8::30]:81', host: '2001:db8::30', why: 'an IPv6, unbracketed for the record' },
  { url: '10.0.0.31:81', host: '10.0.0.31', why: 'no scheme' },
  { url: 'npm.example.com', host: 'npm.example.com', why: 'a bare host' },
  { url: 'http://localhost:81', host: '', why: 'localhost answers every client with itself' },
  { url: 'http://npm.localhost:81', host: '', why: 'so does any name under .localhost' },
  { url: 'http://127.0.0.1:81', host: '', why: 'the IPv4 loopback' },
  { url: 'http://127.1.2.3', host: '', why: 'all of 127/8 is loopback' },
  { url: 'http://[::1]:81', host: '', why: 'the IPv6 loopback' },
  { url: 'http://0.0.0.0:81', host: '', why: 'an unspecified address' },
  { url: 'not a url', host: '', why: 'nothing that names a host' },
  { url: '', host: '', why: 'no URL' },
  { url: undefined, host: '', why: 'a provider with no URL' },
  { url: null, host: '', why: 'a URL the API sent as null' },
];

describe('proxyHostTarget', () => {
  it.each(PROXY_HOSTS)('$url: $why', ({ url, host }) => {
    expect(proxyHostTarget(url)).toBe(host);
  });
});

// The host as a URL parser reads it, since an integration's URL is a whole one: the loopback
// has more spellings than the four above, and a name of one label is a container's name,
// which nothing on the LAN resolves, as are the names a container runtime gives the host.
// None of these is an address a LAN record can point at. An IPv4 address mapped into IPv6 is
// the one spelling read back, as the IPv4 address it carries.
const PROXY_HOSTS_AS_PARSED: Array<{ url: string; host: string; why: string }> = [
  { url: 'http://[0:0:0:0:0:0:0:1]:81', host: '', why: 'the IPv6 loopback written out in full' },
  { url: 'http://[::ffff:127.0.0.1]:81', host: '', why: 'the IPv4 loopback mapped into IPv6' },
  { url: 'http://127.1:81', host: '', why: 'the IPv4 loopback in its short form' },
  { url: 'http://[0:0:0:0:0:0:0:0]:81', host: '', why: 'the unspecified IPv6 address written out in full' },
  { url: 'http://0:81', host: '', why: 'the unspecified IPv4 address as one number' },
  { url: 'http://npm:81', host: '', why: 'a name of one label, the way Docker names a container' },
  { url: 'http://operator@10.0.0.30:81', host: '', why: 'a login before the host' },
  { url: 'http://LOCALHOST.:81', host: '', why: 'localhost in capitals, with the root dot' },
  { url: 'http://app.localhost:81', host: '', why: 'a name under .localhost' },
  { url: '2001:db8::1', host: '', why: 'a bare IPv6 address is not a URL' },
  { url: 'http://10.0.0.30:81/api?probe=1', host: '10.0.0.30', why: 'a path and a query, dropped' },
  { url: 'http://[::ffff:10.0.0.30]:81', host: '10.0.0.30', why: 'an IPv4 address mapped into IPv6 is that address, dotted' },
  { url: 'http://[::ffff:0.0.0.0]:81', host: '', why: 'the unspecified IPv4 address mapped into IPv6' },
  { url: 'http://[::127.0.0.1]:81', host: '', why: 'the IPv4-compatible loopback RFC 4291 deprecated' },
  { url: 'http://[::10.0.0.30]:81', host: '', why: 'any IPv4-compatible address: all of ::/96 is refused' },
  { url: 'http://host.docker.internal:81', host: '', why: 'the host as Docker names it, which only a container resolves' },
  { url: 'http://docker.internal:81', host: '', why: 'the name itself, as well as the names under it' },
  { url: 'http://host.containers.internal:81', host: '', why: 'the same host as Podman names it' },
  { url: 'http://npm.:81', host: '', why: 'a name of one label with the root dot is still one label' },
  { url: 'ftp://10.0.0.30', host: '', why: 'a scheme other than the two the panel reaches a proxy with' },
];

describe('proxyHostTarget, on the host a URL parser reads', () => {
  it.each(PROXY_HOSTS_AS_PARSED)('$url: $why', ({ url, host }) => {
    expect(proxyHostTarget(url)).toBe(host);
  });
});

// An address followed by more than one root dot: a URL parser takes one root dot off before
// it looks for a number at the end, so `127.1..` still ends in an empty label and is kept as
// a name, spelled as it came. Its root dots off, the rest is read again as the parser reads
// an address, and judged as that address.
const PROXY_HOSTS_UNDER_ROOT_DOTS: Array<{ url: string; host: string; why: string }> = [
  { url: 'http://127.1..:81', host: '', why: 'the IPv4 loopback in its short form' },
  { url: 'http://127.1...:81', host: '', why: 'the loopback under three root dots, every one taken off' },
  { url: 'http://0x7f.1..:81', host: '', why: 'the loopback with its first part in hex' },
  { url: 'http://0177.0.0.1..:81', host: '', why: 'the loopback with its first part in octal' },
  { url: 'http://0.0..:81', host: '', why: 'the unspecified IPv4 address in its short form' },
  { url: 'http://127.1%2e%2e:81', host: '', why: 'the root dots percent-encoded, which the parser decodes first' },
  { url: 'http://1.2.3.4.5..:81', host: '', why: 'five numbers: it ends in a number and is no address' },
  { url: 'http://10.0.0.30.5..:81', host: '', why: 'the proxy address with one number too many' },
  { url: 'http://10.1..:81', host: '10.0.0.1', why: 'a LAN address in its short form, written out' },
  { url: 'http://10.1...:81', host: '10.0.0.1', why: 'the same under three root dots, every one taken off' },
  { url: 'http://10.0.0.1..:81', host: '10.0.0.1', why: 'a LAN address already written out' },
];

describe('proxyHostTarget, on an address followed by root dots', () => {
  it.each(PROXY_HOSTS_UNDER_ROOT_DOTS)('$url: $why', ({ url, host }) => {
    expect(proxyHostTarget(url)).toBe(host);
  });
});

// Brackets hold an IPv6 address and nothing else: around anything else, the value is left in
// the field as it was typed, instead of becoming a host no resolver would take.
const NOT_BRACKETED_IPV6: Array<{ value: string; why: string }> = [
  { value: 'http://[not-an-ip]:80', why: 'a name in brackets, in a URL' },
  { value: '[not-an-ip]:80', why: 'a name in brackets, before a port' },
];

describe('parseTarget, on brackets', () => {
  it.each(NOT_BRACKETED_IPV6)('leaves $value alone: $why', ({ value }) => {
    expect(parseTarget(value)).toBeNull();
  });
});
