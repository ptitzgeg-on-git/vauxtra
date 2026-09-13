/**
 * The body of the "this domain is still in use" dialog on the DNS tab.
 *
 * The sentence it replaces said "Deleting it may break existing routes", and that is not
 * what happens. Nothing at runtime reads the `domains` table: the scheduler, the DNS push
 * and the proxy push all work off `services.domain`, which holds the name as plain text
 * with no reference declared, and the only readers of `domains` are the list route, the
 * pickers it feeds and the backup. A service on a deleted domain keeps its hostname and
 * goes on being published exactly as it was. Warning about broken routes is the kind of
 * false alarm that gets a dialog clicked through without being read.
 *
 * Three things do happen, and none of them were on the screen:
 *
 *   -- the name leaves the pickers (`ExposeModal`, `TemplateModal`, and the default the
 *      Docker discovery proposes), so the next service cannot be created under it;
 *   -- a service template built on the name was never counted at all. The old dialog read
 *      `services` and nothing else, so a domain only a template used showed "0 services"
 *      and got the plain confirmation, with nothing on screen saying anything held it;
 *   -- and the name comes back on its own. `INSERT OR IGNORE INTO domains` runs in the
 *      Docker discovery (`app/api/docker.py`) and in both provider imports
 *      (`app/api/sync.py`), so the next scan that brings in a service under this root
 *      re-creates the row and the list disagrees with itself between two visits.
 *
 * The server writes the same three facts to the journal (`_log_domain_removal` in
 * `app/api/settings.py`), because this dialog is the only place they are said and it is
 * gone the moment it is answered.
 */
import { useT } from '@/i18n';

export interface DomainDependent {
  id: number;
  /** The FQDN of a service, or the name of a service template. */
  label: string;
}

interface Props {
  /** The root domain about to be deleted. */
  domain: string;
  /** Services built on it, already sorted. */
  services: DomainDependent[];
  /** Service templates built on it, already sorted. */
  templates: DomainDependent[];
}

/** Past this many rows the list stops naming and starts counting. */
const MAX_ROWS = 5;

function DependentList({ rows, mono }: { rows: DomainDependent[]; mono?: boolean }) {
  const t = useT();
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
      {rest > 0 && <li className="text-xs">{t('settings.dns.confirm.in_use_more', { count: rest })}</li>}
    </ul>
  );
}

export function DomainDeleteBody({ domain, services, templates }: Props) {
  const t = useT();
  return (
    <div className="space-y-3">
      {services.length > 0 && (
        <>
          <p>{t('settings.dns.confirm.in_use_services', { count: services.length, domain })}</p>
          <DependentList rows={services} mono />
        </>
      )}

      {templates.length > 0 && (
        <>
          <p>{t('settings.dns.confirm.in_use_templates', { count: templates.length, domain })}</p>
          <DependentList rows={templates} />
        </>
      )}

      <p>{t('settings.dns.confirm.in_use_effect')}</p>
      <p>{t('settings.dns.confirm.in_use_pickers', { domain })}</p>
      <p>{t('settings.dns.confirm.in_use_returns', { domain })}</p>
    </div>
  );
}
