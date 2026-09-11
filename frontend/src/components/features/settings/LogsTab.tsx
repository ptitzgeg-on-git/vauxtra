import { useEffect, useMemo, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { ChevronLeft, ChevronRight, FileTerminal, Radio, RefreshCw, Trash2 } from 'lucide-react';
import { API_BASE_URL, api } from '@/api/client';
import { useT } from '@/i18n';
import { useFormat } from '@/hooks/useFormat';
import { cn } from '@/lib/cn';
import { translateApiError } from '@/lib/errors';
import {
  Badge,
  Button,
  Chip,
  ChipGroup,
  EmptyState,
  Field,
  IconButton,
  InlineAlert,
  SearchInput,
  Select,
  SkeletonRow,
  Switch,
  useConfirmDialog,
  type Tone,
} from '@/components/ui';
import type { LogEntry, LogLevel, LogsResponse } from '@/types/api';
import { SettingsSection } from './SettingsSection';

// The levels `add_log` writes. `ok` was in this list and no row has ever carried it, so the
// chip filtered every row away; `warn` is not here because `GET /api/logs` folds it into
// `warning` (`app/api/settings.py`), which is the only place the two spellings meet.
const LEVELS: readonly LogLevel[] = ['info', 'warning', 'error'];
const LEVEL_TONE: Record<string, Tone> = { ok: 'success', info: 'info', warning: 'warning', warn: 'warning', error: 'danger' };
const PER_PAGE_OPTIONS = [25, 50, 100, 200] as const;
const LIVE_CAP = 500;
const POLL_MS = 5000;

type LiveState = 'idle' | 'connecting' | 'open' | 'fallback';

function levelKey(level: string): string {
  return level === 'warn' ? 'settings.logs.level_warning' : `settings.logs.level_${level}`;
}

/** The action log: level and text filters, pages, a live stream, and the clear button. */
export function LogsTab() {
  const t = useT();
  const queryClient = useQueryClient();
  const { formatDateTime } = useFormat();
  const { confirm, ConfirmDialogElement } = useConfirmDialog();

  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState<number>(50);
  const [level, setLevel] = useState<LogLevel | ''>('');
  const [search, setSearch] = useState('');
  const [live, setLive] = useState(false);
  const [liveState, setLiveState] = useState<LiveState>('idle');
  const [liveEntries, setLiveEntries] = useState<LogEntry[]>([]);

  const sseSupported = typeof EventSource !== 'undefined';
  const polling = live && (!sseSupported || liveState === 'fallback');

  const logsQuery = useQuery<LogsResponse>({
    queryKey: ['logs', 'page', page, perPage, level],
    queryFn: () => api.get<LogsResponse>(`/logs?page=${page}&per_page=${perPage}${level ? `&level=${level}` : ''}`),
    placeholderData: keepPreviousData,
    refetchInterval: polling ? POLL_MS : false,
  });

  useEffect(() => {
    if (!live || !sseSupported) return;
    // Not '/api/...': a build served behind VITE_API_URL has its API somewhere else, and
    // EventSource cannot go through the axios instance that knows where.
    const source = new EventSource(`${API_BASE_URL}/logs/stream`, { withCredentials: true });
    source.onopen = () => setLiveState('open');
    source.onmessage = (event: MessageEvent<string>) => {
      try {
        const entry = JSON.parse(event.data) as Partial<LogEntry>;
        if (!entry || typeof entry.id !== 'number' || typeof entry.message !== 'string') return;
        const full: LogEntry = {
          id: entry.id,
          level: (entry.level ?? 'info') as LogLevel,
          message: entry.message,
          created_at: entry.created_at ?? '',
        };
        setLiveEntries((prev) => (prev.some((e) => e.id === full.id) ? prev : [full, ...prev].slice(0, LIVE_CAP)));
      } catch {
        // A malformed frame is dropped; the next one stands on its own.
      }
    };
    source.onerror = () => {
      source.close();
      setLiveState('fallback');
    };
    return () => source.close();
  }, [live, sseSupported]);

  const clearLogs = useMutation({
    mutationFn: () => api.post('/logs/clear'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['logs'] });
      setLiveEntries([]);
      setPage(1);
      toast.success(t('settings.logs.cleared'));
    },
    onError: (err: unknown) => toast.error(translateApiError(err, t, t('settings.logs.clear_failed'))),
  });

  const toggleLive = (checked: boolean) => {
    setLive(checked);
    setLiveState(checked ? 'connecting' : 'idle');
    if (!checked) {
      setLiveEntries([]);
      queryClient.invalidateQueries({ queryKey: ['logs'] });
    }
  };

  const requestClear = async () => {
    const ok = await confirm({
      title: t('settings.logs.clear_title'),
      message: t('settings.logs.clear_message'),
      confirmLabel: t('settings.logs.clear'),
      variant: 'danger',
    });
    if (ok) clearLogs.mutate();
  };

  const changeLevel = (next: LogLevel | '') => {
    setLevel(next);
    setPage(1);
  };

  const data = logsQuery.data;
  const pages = Math.max(1, data?.pages ?? 1);
  const needle = search.trim().toLowerCase();

  const rows = useMemo(() => {
    const matches = (entry: LogEntry) =>
      (!level || entry.level === level) &&
      (!needle ||
        entry.message.toLowerCase().includes(needle) ||
        entry.level.toLowerCase().includes(needle) ||
        entry.created_at.toLowerCase().includes(needle));
    const liveIds = new Set(liveEntries.map((e) => e.id));
    const fromLive = liveEntries.filter(matches).map((entry) => ({ entry, live: true }));
    const fromPage = (data?.items ?? [])
      .filter((entry) => !liveIds.has(entry.id))
      .filter(matches)
      .map((entry) => ({ entry, live: false }));
    return [...fromLive, ...fromPage];
  }, [liveEntries, data?.items, level, needle]);

  const total = (data?.total ?? 0) + liveEntries.length;
  // The level chip is a server-side filter, so `total` already reflects it. The text search
  // is not: it runs over the page in hand. The footer therefore has to say which of the two
  // numbers it is showing, instead of printing a 21 under three visible rows.
  const searching = needle.length > 0;
  // Whether anything is narrowing the list at all -- which is what separates "this instance
  // has never logged a thing" from "your filter hides everything".
  const filtering = searching || level !== '';

  return (
    <div className="space-y-6">
      <SettingsSection
        icon={<FileTerminal />}
        title={t('settings.logs.title')}
        description={t('settings.logs.desc')}
        actions={
          <>
            <Switch
              size="sm"
              checked={live}
              onCheckedChange={toggleLive}
              label={
                <span className="inline-flex items-center gap-1.5">
                  <Radio aria-hidden="true" className={cn('h-3.5 w-3.5', live && liveState === 'open' && 'text-success')} />
                  {t('settings.logs.live')}
                </span>
              }
            />
            <IconButton
              label={t('settings.logs.refresh')}
              icon={<RefreshCw className={cn(logsQuery.isFetching && 'animate-spin')} />}
              tooltip
              variant="outline"
              onClick={() => logsQuery.refetch()}
            />
            <Button
              variant="danger"
              size="sm"
              leftIcon={<Trash2 />}
              loading={clearLogs.isPending}
              disabled={(data?.total ?? 0) === 0 && liveEntries.length === 0}
              onClick={() => void requestClear()}
            >
              {t('settings.logs.clear')}
            </Button>
          </>
        }
      >
        {live && liveState === 'fallback' && (
          <InlineAlert tone="warning" title={t('settings.logs.live_fallback_title')}>
            {t('settings.logs.live_fallback_desc', { seconds: POLL_MS / 1000 })}
          </InlineAlert>
        )}
        {live && !sseSupported && (
          <InlineAlert tone="info">{t('settings.logs.live_unsupported', { seconds: POLL_MS / 1000 })}</InlineAlert>
        )}

        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <ChipGroup label={t('settings.logs.level_filter_aria')}>
            <Chip size="sm" tone="primary" selected={level === ''} onClick={() => changeLevel('')}>
              {t('settings.logs.level_all')}
            </Chip>
            {LEVELS.map((value) => (
              <Chip
                key={value}
                size="sm"
                tone={LEVEL_TONE[value]}
                selected={level === value}
                onClick={() => changeLevel(level === value ? '' : value)}
              >
                {t(levelKey(value))}
              </Chip>
            ))}
          </ChipGroup>
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder={t('settings.logs.search_placeholder')}
            wrapperClassName="lg:w-80"
          />
        </div>

        {logsQuery.isLoading ? (
          <div className="divide-y divide-border rounded-xl border border-border">
            {Array.from({ length: 6 }, (_, i) => (
              <SkeletonRow key={i} columns={3} />
            ))}
          </div>
        ) : logsQuery.isError ? (
          <InlineAlert
            tone="danger"
            title={t('settings.logs.load_failed')}
            action={
              <Button variant="outline" size="sm" onClick={() => logsQuery.refetch()}>
                {t('ui.error.retry')}
              </Button>
            }
          >
            {translateApiError(logsQuery.error, t, t('common.error'))}
          </InlineAlert>
        ) : rows.length === 0 ? (
          <EmptyState
            compact
            icon={<FileTerminal />}
            title={total === 0 && !filtering ? t('settings.logs.empty') : t('settings.logs.no_match')}
          />
        ) : (
          <ol
            aria-live={live ? 'polite' : undefined}
            className={cn('divide-y divide-border rounded-xl border border-border', logsQuery.isFetching && 'opacity-80')}
          >
            {rows.map(({ entry, live: isLive }) => (
              <li
                key={entry.id}
                className={cn(
                  'flex flex-col gap-1 px-4 py-2.5 text-sm sm:flex-row sm:items-start sm:gap-4',
                  // `slide-in-from-top` is this project's own utility (index.css); the numeric
                  // `-1` suffix is tailwindcss-animate syntax and that plugin is not installed,
                  // so the class it used to carry compiled to nothing at all.
                  isLive && 'bg-primary/5 animate-in fade-in slide-in-from-top animate-duration-200',
                )}
              >
                <time
                  dateTime={entry.created_at}
                  className="shrink-0 text-xs text-muted-foreground tabular-nums sm:w-44 sm:pt-0.5"
                >
                  {formatDateTime(entry.created_at, 'medium')}
                </time>
                <Badge size="sm" tone={LEVEL_TONE[entry.level] ?? 'neutral'} dot className="shrink-0 self-start">
                  {t(levelKey(entry.level))}
                </Badge>
                <span className="min-w-0 flex-1 wrap-break-word text-foreground">{entry.message}</span>
              </li>
            ))}
          </ol>
        )}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground tabular-nums">
            {t('settings.logs.page_of', { page, pages })}
            <span aria-hidden="true"> · </span>
            {searching ? t('settings.logs.total_matching', { count: rows.length, total }) : t('settings.logs.total', { count: total })}
          </p>
          <div className="flex items-center gap-2">
            <Field inline label={t('settings.logs.per_page')} className="mr-2">
              <Select
                size="sm"
                value={perPage}
                onChange={(e) => {
                  setPerPage(Number(e.target.value));
                  setPage(1);
                }}
              >
                {PER_PAGE_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </Select>
            </Field>
            <IconButton
              label={t('settings.logs.prev_page')}
              icon={<ChevronLeft />}
              variant="outline"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            />
            <IconButton
              label={t('settings.logs.next_page')}
              icon={<ChevronRight />}
              variant="outline"
              disabled={page >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            />
          </div>
        </div>
      </SettingsSection>
      {ConfirmDialogElement}
    </div>
  );
}
