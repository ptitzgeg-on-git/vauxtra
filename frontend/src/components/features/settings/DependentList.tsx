/**
 * The short list of rows a deletion is about to change, under the sentence that counted them.
 *
 * Both "still in use" dialogs in Settings show the same thing: a count, then the names behind
 * it, then a tail saying how many were not named. They were written twice, with two copies of
 * the row cap -- and a cap that drifts between two dialogs makes the shorter one look like the
 * whole truth.
 *
 * The tail arrives as a function rather than a locale key, because each dialog keeps its own
 * namespace and a key passed in as a string would be a `t()` call this component builds at
 * runtime. `check-locale-usage.mjs` reads literal keys and cannot follow one of those, so the
 * two sentences would have quietly left the set of strings it verifies.
 */

export interface Dependent {
  id: number;
  /** The FQDN of a service, or the name of a service template. */
  label: string;
}

interface Props {
  rows: Dependent[];
  /** The "and {count} more" tail, written by the caller so its key stays a literal. */
  more: (count: number) => string;
  /** Hostnames read as addresses and are set in the mono face; names of things are not. */
  mono?: boolean;
}

/** Past this many rows the list stops naming and starts counting. */
const MAX_ROWS = 5;

export function DependentList({ rows, more, mono }: Props) {
  const rest = rows.length - MAX_ROWS;
  return (
    <ul className="space-y-1">
      {rows.slice(0, MAX_ROWS).map((row) => (
        <li
          key={row.id}
          className={mono ? 'truncate font-mono text-xs text-foreground' : 'truncate text-xs font-medium text-foreground'}
        >
          {row.label}
        </li>
      ))}
      {rest > 0 && <li className="text-xs">{more(rest)}</li>}
    </ul>
  );
}
