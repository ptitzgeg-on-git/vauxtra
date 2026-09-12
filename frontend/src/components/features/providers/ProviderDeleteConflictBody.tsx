/**
 * The body of the "services still depend on it" dialog, shared by the Integrations page and
 * by the Setup wizard so the two never drift apart again.
 *
 * It used to be a single sentence: "they will stop being published through it". That was
 * false for half the list. A service can name several targets -- a second DNS server, a
 * second proxy -- and removing one of them leaves the others publishing the hostname exactly
 * as before. The API now says which is which (`still_published`), and the rows carry it.
 *
 * The second thing the old dialog never said: deleting an integration in Vauxtra changes
 * nothing on the integration. The records it already serves stay live on it, and Vauxtra
 * loses the ability to see them, let alone remove them. Hence the checkbox, which maps to
 * `?force=true&withdraw=true`.
 */
import { useState } from 'react';
import { useT } from '@/i18n';
import { WITHDRAW_BY_DEFAULT, type WithdrawChoice } from '@/hooks/useProviderMutations';
import type { ProviderDeleteConflict } from '@/types/api';

interface Props {
  /** The integration being deleted. */
  name: string;
  detail: ProviderDeleteConflict;
  choiceRef: WithdrawChoice;
}

const MAX_ROWS = 5;

export function ProviderDeleteConflictBody({ name, detail, choiceRef }: Props) {
  const t = useT();
  // Not `choiceRef.current`: reading a ref during render is exactly what it is not for. The
  // box starts on the same constant, so the two cannot disagree.
  const [withdraw, setWithdraw] = useState(WITHDRAW_BY_DEFAULT);

  const services = detail.services ?? [];
  const kept = services.filter((s) => s.still_published);
  const dark = services.filter((s) => !s.still_published);
  const rest = services.length - MAX_ROWS;

  const toggle = (next: boolean) => {
    choiceRef.current = next;
    setWithdraw(next);
  };

  return (
    <div className="space-y-3">
      <p>{t('providers.delete.deps_intro', { count: services.length, name })}</p>

      <ul className="space-y-1">
        {services.slice(0, MAX_ROWS).map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-medium text-foreground">{s.fqdn}</span>
            {s.roles?.length ? <span className="text-xs">({s.roles.join(', ')})</span> : null}
            <span
              className={
                s.still_published
                  ? 'rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground'
                  : 'rounded-full bg-warning/10 px-2 py-0.5 text-[11px] font-medium text-warning'
              }
            >
              {t(s.still_published ? 'providers.delete.dep_kept_tag' : 'providers.delete.dep_dark_tag')}
            </span>
          </li>
        ))}
        {rest > 0 && <li className="text-xs">{t('providers.delete.deps_more', { count: rest })}</li>}
      </ul>

      {dark.length > 0 && <p>{t('providers.delete.deps_dark', { count: dark.length })}</p>}
      {kept.length > 0 && <p>{t('providers.delete.deps_kept', { count: kept.length })}</p>}

      <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-border bg-muted/40 p-3">
        <input
          type="checkbox"
          checked={withdraw}
          onChange={(e) => toggle(e.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-warning"
        />
        <span className="min-w-0">
          <span className="block text-sm font-medium text-foreground">
            {t('providers.delete.withdraw_label', { name })}
          </span>
          <span className="mt-0.5 block text-xs">{t('providers.delete.withdraw_hint', { name })}</span>
        </span>
      </label>
    </div>
  );
}
