/**
 * The body of the "this label is in use" dialog on the Settings taxonomy tab.
 *
 * The sentence it replaces was one line for both kinds of label and both kinds of holder:
 * "{name} will be removed from every service that carries it." True, and it named no number,
 * so a tag on one service and a tag on forty asked the same question. Every other deletion in
 * this product counts what it is about to change -- a provider names the services, the
 * templates and the webhooks that point at it, a root domain names the services and templates
 * built on it -- and the two label lists, which are the easiest thing here to delete by
 * accident, counted nothing.
 *
 * The second half was not said at all. A tag is held in two places that behave nothing alike:
 *
 *   -- `service_tags` declares `ON DELETE CASCADE` (`app/models.py`), so the services are
 *      unlinked on the spot. They keep their hostname and go on being published; what they
 *      lose is a label somebody was filtering and grouping by;
 *   -- `service_templates.tag_ids_json` is TEXT holding a JSON array, which no constraint
 *      reaches. The id survives the delete and is dropped on the next read by
 *      `_drop_dead_tags` (`app/api/templates.py`), silently and by design -- the alternative
 *      is a template that cannot be saved. So the template comes back one tag shorter, and
 *      the next service built from it starts without the tag, with nothing anywhere saying
 *      why.
 *
 * An environment has only the first of those: `TemplateIn` names tags and never names an
 * environment, so there is no JSON column to rot.
 *
 * `app/api/tags.py` writes the same counts to the journal, because this dialog is the only
 * other place they are said and it is gone the moment it is answered.
 */
import { useT } from '@/i18n';
import { DependentList, type Dependent } from './DependentList';

interface Props {
  /** The label about to be deleted. */
  name: string;
  /** Tags are carried by services and named by templates; environments are only set on services. */
  kind: 'tags' | 'env';
  /** Services holding the label, already sorted. */
  services: Dependent[];
  /** Service templates naming the tag, already sorted. Always empty for an environment. */
  templates: Dependent[];
}

export function TaxonomyDeleteBody({ name, kind, services, templates }: Props) {
  const t = useT();
  return (
    <div className="space-y-3">
      {services.length > 0 && (
        <>
          <p>
            {kind === 'tags'
              ? t('settings.taxonomy.in_use_carried', { count: services.length, name })
              : t('settings.taxonomy.in_use_set', { count: services.length, name })}
          </p>
          <DependentList
            rows={services}
            mono
            more={(count) => t('settings.taxonomy.in_use_more', { count })}
          />
          <p>{t('settings.taxonomy.in_use_effect')}</p>
        </>
      )}

      {templates.length > 0 && (
        <>
          <p>{t('settings.taxonomy.in_use_templates', { count: templates.length, name })}</p>
          <DependentList
            rows={templates}
            more={(count) => t('settings.taxonomy.in_use_more', { count })}
          />
          <p>{t('settings.taxonomy.in_use_templates_effect')}</p>
        </>
      )}
    </div>
  );
}
