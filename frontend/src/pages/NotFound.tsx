/**
 * What an unknown address answers.
 *
 * Every path the router did not match used to render `<Navigate to="/" replace />`: the
 * dashboard appeared, and `replace` wiped the address that had been asked for out of the
 * history, so a stale bookmark, a renamed page and a typo all looked like a working link to
 * the dashboard. `/integrations` -- the word the sidebar itself uses for that section, whose
 * route is `/providers` -- is the easy one to type, and it answered silently.
 *
 * The address is named back to the reader, and the way on is a link rather than a redirect.
 */
import { Link, useLocation } from 'react-router-dom';
import { Compass } from 'lucide-react';
import { useT } from '@/i18n';
import { EmptyState, PageHeader, buttonVariants } from '@/components/ui';

export function NotFound() {
  const t = useT();
  const { pathname } = useLocation();

  return (
    <div className="space-y-6">
      <PageHeader title={t('notfound.title')} description={t('notfound.description')} />
      <EmptyState
        icon={<Compass />}
        title={<span className="font-mono text-sm">{pathname}</span>}
        description={t('notfound.body')}
        action={
          <Link to="/" className={buttonVariants({ variant: 'primary' })}>
            {t('notfound.back')}
          </Link>
        }
      />
    </div>
  );
}
