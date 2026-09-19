import { Check, ExternalLink, Heart, Languages } from 'lucide-react';
import { SUPPORTED_LANGUAGES, useI18n } from '@/i18n';
import { cn } from '@/lib/cn';
import { buttonVariants } from '@/components/ui';
import { SettingsSection } from './SettingsSection';

const CONTRIBUTE_URL = 'https://github.com/ptitzgeg-on-git/vauxtra/blob/main/CONTRIBUTING.md#translations';

/** Display language: one card of language buttons, one invitation to contribute a locale. */
export function LanguageTab() {
  const { lang, setLang, t } = useI18n();

  return (
    <div className="space-y-6">
      <SettingsSection
        icon={<Languages />}
        title={t('settings.language.title')}
        description={t('settings.language.description')}
      >
        <div role="radiogroup" aria-label={t('settings.language.title')} className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {SUPPORTED_LANGUAGES.map((language) => {
            const selected = language.code === lang;
            return (
              <button
                key={language.code}
                type="button"
                role="radio"
                aria-checked={selected}
                lang={language.code}
                onClick={() => setLang(language.code)}
                className={cn(
                  'flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left text-sm transition-colors duration-150',
                  'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
                  selected
                    ? 'border-primary bg-primary/5 text-foreground'
                    : 'border-border bg-card text-muted-foreground hover:bg-accent hover:text-foreground',
                )}
              >
                <span aria-hidden="true" className="text-xl leading-none">
                  {language.flag}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{language.label}</span>
                  <span className="block text-[11px] uppercase tracking-wider text-muted-foreground">{language.code}</span>
                </span>
                {selected && <Check aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />}
              </button>
            );
          })}
        </div>
      </SettingsSection>

      <SettingsSection
        tone="info"
        icon={<Heart />}
        title={t('settings.language.contribute')}
        description={t('settings.language.contribute_description')}
        actions={
          <a
            href={CONTRIBUTE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonVariants({ variant: 'outline', size: 'sm' })}
          >
            {t('settings.language.contribute_link')}
            <ExternalLink aria-hidden="true" className="ml-1.5 h-3.5 w-3.5" />
          </a>
        }
      />
    </div>
  );
}
