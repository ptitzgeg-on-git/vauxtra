/**
 * What the form reads out of an address it is given, as pure functions a table can hold.
 *
 * The target field takes a host, and a URL or a `host:port` it splits into the three fields
 * the route needs. It used to split on every keystroke, through `new URL`, which broke what
 * was still being typed (ux-audit.md § 1.10): `http://grafana` lost its scheme before the
 * dot came, and `http://1` became `0.0.0.1`, because the browser's URL parser reads a
 * numeric host as an IPv4 address. `parseTarget` does not go through `new URL`, and the form
 * calls it on paste, when the field is left and on "Continue", never on a keystroke.
 *
 * `proxyHostTarget` does: an integration's URL is a whole one, saved and not being typed, and
 * the parser writes each spelling of an address one way (`127.1` is `127.0.0.1` to it, and
 * `[0:0:0:0:0:0:0:1]` is `[::1]`), so the loopbacks it has to refuse make a short list. Two
 * spellings it does not settle. An IPv4 address carried in IPv6 it writes in hex
 * (`[::ffff:10.0.0.30]` is `[::ffff:a00:1e]`): that one is read back as the IPv4 address. An
 * address followed by more than one root dot it keeps as a name (`127.1..` stays `127.1..`):
 * its root dots off, that one is read again as the parser reads an address.
 */

export type ParsedTarget = {
  /** Without brackets: the API takes an IPv6 target bare, the way `ipaddress` reads it. */
  host: string;
  /** Absent when the value named none; the form then picks one from the scheme. */
  port?: number;
  scheme?: 'http' | 'https';
};

// A host runs up to the port, the path or the end. It holds no `@`, so a value that carries
// a login before the host matches neither shape and stays in the field as it was typed.
const WITH_SCHEME = /^(https?):\/\/(\[[^\]]+\]|[^/:?#@\s[\]]+)(?::(\d+))?(?:[/?#].*)?$/i;
const HOST_AND_PORT = /^(\[[^\]]+\]|[^/:?#@\s[\]]+):(\d+)$/;
// What brackets may hold: hex digits, colons and the dots of an IPv4 tail, one colon at least.
const BRACKETED_IPV6 = /^\[[0-9a-f]*:[0-9a-f:.]*\]$/i;

function splitAddress(text: string): { host: string; port?: string; scheme?: string } | null {
  const url = WITH_SCHEME.exec(text);
  if (url) return { scheme: url[1], host: url[2], port: url[3] };
  const hostAndPort = HOST_AND_PORT.exec(text);
  if (hostAndPort) return { host: hostAndPort[1], port: hostAndPort[2] };
  return null;
}

/**
 * The scheme, host and port a pasted or typed address names, or `null` when there is nothing
 * to split: a bare host, a bare IPv6 address (more than one colon, so no port can be told
 * apart from it), or text that is not an address. A port outside 1 to 65535 is not split
 * either, nor brackets around anything but an IPv6 address, so the value stays in the field
 * where the operator can see what is wrong with it.
 */
export function parseTarget(value: string): ParsedTarget | null {
  const parts = splitAddress(value.trim());
  if (!parts) return null;
  if (parts.host.startsWith('[') && !BRACKETED_IPV6.test(parts.host)) return null;
  const host = parts.host.startsWith('[') ? parts.host.slice(1, -1) : parts.host;
  if (!host) return null;
  const parsed: ParsedTarget = { host };
  if (parts.scheme) parsed.scheme = parts.scheme.toLowerCase() === 'https' ? 'https' : 'http';
  if (parts.port) {
    const port = Number(parts.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
    parsed.port = port;
  }
  return parsed;
}

/** The three fields a split address fills, in the shape the form keeps them. */
type TargetFields = { target_ip: string; target_port: number; forward_scheme: 'http' | 'https' };

/**
 * The form with a split address written into the three fields it fills, and the rest left as
 * it was. One rule for a paste, a field left and a "Continue" sent from inside the field.
 */
export function withTargetSplit<T extends TargetFields>(form: T, parsed: ParsedTarget): T {
  return {
    ...form,
    target_ip: parsed.host,
    // A scheme with no port means that scheme's own port, as it does in a browser.
    target_port: parsed.port ?? (parsed.scheme ? (parsed.scheme === 'https' ? 443 : 80) : form.target_port),
    forward_scheme: parsed.scheme ?? form.forward_scheme,
  };
}

const LOOPBACK_V4 = /^127(?:\.\d{1,3}){3}$/;
// An IPv4 address mapped into IPv6 (RFC 4291 § 2.5.5.2), as a URL parser writes it back:
// `::ffff:10.0.0.30` is `::ffff:a00:1e` to it.
const V4_MAPPED = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/;
// All of ::/96, as a URL parser writes it back: the unspecified address, the loopback, and the
// IPv4-compatible form RFC 4291 § 2.5.5.1 deprecated (`::127.0.0.1` is `::7f00:1` to it).
// None of them is an address a client on the LAN reaches a proxy at.
const V4_COMPATIBLE = /^::(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4})?)?$/;
// The names a container runtime gives the host, `host.docker.internal` and
// `host.containers.internal` among them: only a container resolves them.
const CONTAINER_ONLY = /(?:^|\.)(?:docker|containers)\.internal$/;
const HAS_SCHEME = /^[a-z][a-z0-9+.-]*:\/\//i;

/**
 * An IPv4 address mapped into IPv6, written back as that IPv4 address, dotted: the form shows
 * it as the operator knows it, and the IPv4 rules below refuse or keep it. Any other host is
 * returned as it came.
 */
function unmapped(host: string): string {
  const mapped = V4_MAPPED.exec(host.toLowerCase());
  if (!mapped) return host;
  const high = parseInt(mapped[1], 16);
  const low = parseInt(mapped[2], 16);
  return [high >> 8, high & 255, low >> 8, low & 255].join('.');
}

/**
 * The host without its root dots, read again as a URL parser reads a host. The parser takes
 * one root dot off before it looks for a number at the end, the mark of an address; behind
 * two, the host still ends in an empty label and is kept as a name, so `127.1..` came to the
 * rules below as `127.1` and passed for a name. Read again, `127.1` is `127.0.0.1`, `10.1` is
 * `10.0.0.1`, and `1.2.3.4.5`, which ends in a number and is no address, reads as nothing. An
 * IPv6 address never carries a root dot, and is returned as it came.
 */
function withoutRootDots(host: string): string {
  const bare = host.replace(/\.+$/, '');
  if (!bare || bare.includes(':')) return bare;
  try {
    return new URL(`http://${bare}`).hostname;
  } catch {
    return '';
  }
}

/**
 * A host that means something only to whoever asks: the machine itself (a loopback, an
 * unspecified address, `localhost`), or the host of a container, under a name only a
 * container resolves. No other client on the LAN reaches the proxy there.
 */
function answersOnlyItself(host: string): boolean {
  const name = host.toLowerCase().replace(/\.+$/, '');
  return (
    name === 'localhost' ||
    name.endsWith('.localhost') ||
    CONTAINER_ONLY.test(name) ||
    LOOPBACK_V4.test(name) ||
    V4_COMPATIBLE.test(name) ||
    name === '0.0.0.0'
  );
}

/**
 * The address a LAN record points at for a route behind this proxy: the host of the URL the
 * panel reaches the proxy on, as a URL parser reads it. Empty when the LAN cannot reach the
 * proxy at that host, since a record pointing there sends its clients nowhere: a loopback or
 * an unspecified address in any spelling (all of ::/96 among them, and `127.1..`, root dots
 * and all), `localhost` and the names under it, the names only a container resolves
 * (`host.docker.internal`), a name of one label (`http://npm:81`, the way a Docker network
 * names a container, which nothing on the LAN resolves, with or without its root dot), a
 * scheme other than http and https, a URL that carries a login or does not read as one, and
 * a host that is no address once its root dots are off (`1.2.3.4.5..`). The form then asks
 * for the address instead. An IPv4 address mapped into IPv6 (`[::ffff:10.0.0.30]`) is that
 * IPv4 address, returned dotted and held to the IPv4 rules, and an address followed by root
 * dots is returned as the parser writes it (`10.1..` is `10.0.0.1`).
 */
export function proxyHostTarget(url?: string | null): string {
  const text = String(url || '').trim();
  if (!text) return '';
  let parsed: URL;
  try {
    parsed = new URL(HAS_SCHEME.test(text) ? text : `http://${text}`);
  } catch {
    return '';
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
  if (parsed.username || parsed.password) return '';
  const host = unmapped(withoutRootDots(parsed.hostname.replace(/^\[(.*)\]$/, '$1')));
  if (!host || answersOnlyItself(host)) return '';
  if (!host.includes('.') && !host.includes(':')) return '';
  return host;
}
