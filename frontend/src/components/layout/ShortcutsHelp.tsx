import { Fragment } from 'react';
import { useT } from '@/i18n';
import { Kbd, Modal } from '@/components/ui';
import { isMacPlatform } from '@/components/ui/_internal';

export interface ShortcutsHelpProps {
  open: boolean;
  onClose: () => void;
}

/** One shortcut: `keys` is a sequence of chords, each chord a set of keys pressed together. */
interface Shortcut {
  label: string;
  keys: string[][];
}

function Keys({ keys, then }: { keys: string[][]; then: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {keys.map((chord, ci) => (
        <Fragment key={ci}>
          {ci > 0 && <span className="px-0.5 text-[10px] text-muted-foreground">{then}</span>}
          {chord.map((key, ki) => (
            <Fragment key={ki}>
              {ki > 0 && (
                <span aria-hidden="true" className="text-[10px] text-muted-foreground">
                  +
                </span>
              )}
              <Kbd size="sm">{key}</Kbd>
            </Fragment>
          ))}
        </Fragment>
      ))}
    </span>
  );
}

function ShortcutList({ title, items, then }: { title: string; items: Shortcut[]; then: string }) {
  return (
    <section>
      <h3 className="mb-2 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">{title}</h3>
      <ul className="divide-y divide-border rounded-xl border border-border">
        {items.map((s) => (
          <li key={s.label} className="flex items-center justify-between gap-4 px-3 py-2 text-sm">
            <span className="text-foreground">{s.label}</span>
            <Keys keys={s.keys} then={then} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The keyboard-shortcut reference, opened with `?`. */
export function ShortcutsHelp({ open, onClose }: ShortcutsHelpProps) {
  const t = useT();
  const mod = isMacPlatform() ? '⌘' : 'Ctrl';
  const then = t('layout.shortcuts.then');

  const global: Shortcut[] = [
    { label: t('layout.shortcuts.open_palette'), keys: [[mod, 'K']] },
    { label: t('layout.shortcuts.open_help'), keys: [['?']] },
    { label: t('layout.shortcuts.close'), keys: [['Esc']] },
  ];

  const navigation: Shortcut[] = [
    { label: t('layout.shortcuts.go_dashboard'), keys: [['G'], ['D']] },
    { label: t('layout.shortcuts.go_services'), keys: [['G'], ['E']] },
    { label: t('layout.shortcuts.go_providers'), keys: [['G'], ['P']] },
    { label: t('layout.shortcuts.go_templates'), keys: [['G'], ['T']] },
    { label: t('layout.shortcuts.go_monitoring'), keys: [['G'], ['M']] },
    { label: t('layout.shortcuts.go_certificates'), keys: [['G'], ['C']] },
    { label: t('layout.shortcuts.go_settings'), keys: [['G'], ['S']] },
  ];

  return (
    <Modal open={open} onClose={onClose} size="sm" title={t('layout.shortcuts.title')} description={t('layout.shortcuts.description')}>
      <div className="space-y-5">
        <ShortcutList title={t('layout.shortcuts.group.global')} items={global} then={then} />
        <ShortcutList title={t('layout.shortcuts.group.navigation')} items={navigation} then={then} />
      </div>
    </Modal>
  );
}
