import { useEffect, useId, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { CommissioningState } from '../api/types';
import { Loading } from '../components/bits';
import { Button } from '../components/ui/button';
import { Card } from '../components/ui/card';
import { dateTime } from '../format';
import { LIGHT_ACTION_LABEL } from '../i18n';
import { useCanAct } from '../state';
import { useCan } from '../user';
import { ConfirmationDialog, ConfirmDevice, useCommissioningChange } from './CommissioningActions';
import { commissioningText as text } from './commissioning-text';
import { DeviceTestButton } from './DeviceTools';

function Acknowledgement({ label, checked, setChecked }: { label: string; checked: boolean; setChecked: (value: boolean) => void }) {
  return (
    <label className="flex min-h-11 items-center gap-3">
      <input type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

export function CommissioningPanel({ homeId }: { homeId: string }) {
  const { admin } = useCan();
  if (!admin) return null;
  return <AdminCommissioning key={homeId} homeId={homeId} />;
}

function AdminCommissioning({ homeId }: { homeId: string }) {
  const id = useId();
  const canAct = useCanAct();
  const state = useQuery({ queryKey: qk.commissioning, queryFn: api.commissioning, enabled: canAct });
  const { hash } = useLocation();
  const review = hash === '#teststatus-apparaten';
  const title = useRef<HTMLHeadingElement>(null);
  const enableButton = useRef<HTMLButtonElement>(null);
  const [slow, setSlow] = useState(false);
  const [enable, setEnable] = useState<{ data: CommissioningState; etag: string } | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [checked, setChecked] = useState(false);
  const [needsReload, setNeedsReload] = useState(false);
  const [notice, setNotice] = useState('');
  const mutation = useCommissioningChange(api.enableCommissioning);
  const data = state.data?.data;
  const current = canAct && state.isSuccess && !state.isFetching && !needsReload;

  useEffect(() => {
    if (!state.isLoading) return;
    const timer = window.setTimeout(() => setSlow(true), 10_000);
    return () => window.clearTimeout(timer);
  }, [state.isLoading]);

  async function refresh() {
    const result = await state.refetch();
    if (result.isSuccess) setNeedsReload(false);
    return result;
  }

  async function prepare() {
    setPreparing(true);
    const result = await refresh();
    setPreparing(false);
    if (result.isSuccess && result.data.data.gate.known && result.data.data.task_required) {
      setChecked(false);
      setEnable(result.data);
    }
  }

  return (
    <Card asChild>
      <section aria-labelledby={id} className="grid gap-4 break-words">
        <h2 ref={title} id={id} tabIndex={-1} className="m-0 text-lg">
          {data?.task_required ? text('taskTitle') : text('protectionTitle')}
        </h2>
        {state.isLoading && <Loading label={text(slow ? 'longWait' : 'loading')} />}
        {!canAct && <p className="note unknown">{text('offline')}</p>}
        {!data && !state.isLoading && <p className="m-0">{text('unknown')}</p>}
        {state.isError && <p className="note error" role="alert">{text('fetchError')}</p>}
        {needsReload && <p className="note error" role="alert">{text('mutationError')}</p>}
        {data && (!canAct || state.isError) && (
          <p className="note stale">{text('stale', { time: dateTime(new Date(state.dataUpdatedAt).toISOString()) })}</p>
        )}
        {(state.isError || needsReload) && <Button disabled={!canAct || state.isFetching} onClick={refresh}>{text('retry')}</Button>}
        {data && !data.gate.known && <p className="note unknown">{text('configUnknown')}</p>}
        {data?.task_required && (
          <>
            <p className="m-0 max-w-prose">{text('taskBody')}</p>
            <p className="m-0">{text('counts', { count: data.untested_count, unsupportedCount: data.unsupported_count })}</p>
          </>
        )}
        {data?.gate.known && data.gate.enabled && data.gate.enabled_at && (
          <p className="m-0">{text('enabled', { time: dateTime(data.gate.enabled_at) })}</p>
        )}
        {notice && <p role="status" className="m-0">{notice}</p>}
        {data && (
          <div className="flex flex-wrap gap-3">
            {!review && <Button asChild><Link to="#teststatus-apparaten">{text('review')}</Link></Button>}
            {data.gate.known && data.task_required && (
              <Button ref={enableButton} variant="primair" disabled={!current || mutation.isPending} onClick={prepare}>
                {preparing ? text('refreshing') : text('enable')}
              </Button>
            )}
          </div>
        )}
        {review && <CommissioningDevices homeId={homeId} current={current && !!data?.gate.known} />}
        {enable && (
          <ConfirmationDialog title={text('enableTitle')} description={text('enableBody')}
            busy={mutation.isPending} returnFocus={enableButton.current} close={() => setEnable(null)}>
            <p className="m-0">{text('remaining', { count: enable.data.affected_count })}</p>
            <Acknowledgement label={text('enableCheck')} checked={checked} setChecked={setChecked} />
            <Button variant="primair" disabled={!checked || !current || mutation.isPending}
              onClick={() => mutation.mutate(enable.etag, {
                onSuccess: async () => {
                  setEnable(null);
                  const result = await refresh();
                  if (result.isSuccess && result.data.data.gate.known && result.data.data.gate.enabled) {
                    setNotice('De beveiliging is ingeschakeld.');
                    title.current?.focus();
                  }
                },
                onError: () => { setEnable(null); setNeedsReload(true); },
              })}>{mutation.isPending ? text('enableBusy') : text('enable')}</Button>
          </ConfirmationDialog>
        )}
      </section>
    </Card>
  );
}

function CommissioningDevices({ homeId, current }: { homeId: string; current: boolean }) {
  const canAct = useCanAct();
  const headingId = useId();
  const bulkReasonId = useId();
  const section = useRef<HTMLElement>(null);
  useEffect(() => { section.current?.focus(); }, []);
  const [selected, setSelected] = useState<string[]>([]);
  const [bulk, setBulk] = useState<{ ids: string[]; names: string[]; etag: string } | null>(null);
  const [checked, setChecked] = useState(false);
  const [needsReload, setNeedsReload] = useState(false);
  const [notice, setNotice] = useState('');
  const list = useInfiniteQuery({
    queryKey: qk.commissioningDevices(homeId),
    queryFn: ({ pageParam }) => api.commissioningDevices(homeId, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled: canAct,
  });
  const mutation = useCommissioningChange(({ ids, etag }: { ids: string[]; etag: string }) => api.confirmDeviceTests(ids, etag));
  const pages = list.data?.pages ?? [];
  const devices = pages.flatMap((page) => page.items);
  const observed = pages[0]?.observed_at;
  const etag = pages[0]?.gate_etag;
  const drift = pages.some((page) => page.gate_etag !== etag);
  const available = current && list.isSuccess && !list.isFetching && !needsReload && !drift;
  const selection = devices.filter((device) => selected.includes(device.id) && device.bulk_confirmation_allowed);
  const canConfirm = available && !!etag && selection.length > 0 && selection.length === selected.length;
  return (
    <section ref={section} id="teststatus-apparaten" tabIndex={-1} aria-labelledby={headingId} className="grid min-w-0 gap-4">
      <h3 id={headingId} className="m-0">{text('listTitle')}</h3>
      {list.isLoading && <Loading label={text('loading')} />}
      {list.isFetching && !list.isLoading && <p role="status" className="m-0">{text('refreshing')}</p>}
      {(list.isError || needsReload || drift) && (
        <div className="stack">
          <p className="note error" role="alert">{text(list.isError ? 'fetchError' : 'mutationError')}</p>
          <Button disabled={!canAct || list.isFetching} onClick={async () => {
            const result = await list.refetch();
            if (result.isSuccess) {
              const eligible = new Set(result.data.pages.flatMap((page) => page.items)
                .filter((device) => device.bulk_confirmation_allowed).map((device) => device.id));
              setSelected((ids) => ids.filter((id) => eligible.has(id)));
              setNeedsReload(false);
            }
          }}>{text('retry')}</Button>
        </div>
      )}
      {list.isSuccess && !devices.length && <p>{text('empty')} <a href="#apparaat-toevoegen">{text('add')}</a></p>}
      {observed && <p className="small m-0">{text('source', { time: dateTime(observed) })} {text('historyHint')}</p>}
      {notice && <p role="status" className="m-0">{notice}</p>}
      {devices.length > 0 && (
        <fieldset className="m-0 grid min-w-0 gap-3 rounded-lg border border-solid border-[var(--border-strong)] p-3">
          <legend>{text('selection', { count: selected.length })}</legend>
          <ul className="plain-list grid gap-3">
            {devices.map((device) => (
              <li key={device.id} className="grid min-w-0 gap-3 rounded-lg border border-solid border-[var(--border)] p-3 md:grid-cols-4">
                <div className="min-w-0">
                  <strong>{device.name}</strong>
                  <p className="small m-0">{device.room_name ?? device.home_name}</p>
                  {device.bulk_confirmation_allowed && (
                    <label className="flex min-h-11 items-center gap-2">
                      <input type="checkbox" checked={selected.includes(device.id)} disabled={!available || (!selected.includes(device.id) && selected.length >= 200)}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setSelected((ids) => checked ? [...ids, device.id] : ids.filter((id) => id !== device.id));
                        }} />
                      <span>{text('select', { name: device.name })}</span>
                    </label>
                  )}
                </div>
                <div>
                  <strong>{text('status')}</strong>
                  <p className="m-0">{device.compatibility === 'tested' ? text('tested') :
                    device.compatibility === 'unsupported' ? text('unsupported') :
                    device.compatibility === 'untested' ? text('untested') : 'Onbekend'}</p>
                  {device.blocked_when_enabled && !device.protected && device.compatibility === 'tested' && (
                    <p className="small m-0">Een ongetest onderdeel blokkeert de bediening als de beveiliging aan staat.</p>
                  )}
                  {device.protected && <p className="small m-0">{text('protected')}</p>}
                  {device.compatibility === 'unsupported' && <p className="small m-0">{text('unsupportedHelp')}</p>}
                </div>
                <div>
                  <strong>{text('lastConfirmed')}</strong>
                  <p className="m-0">{device.last_confirmed_command ? text('confirmedDetail', {
                    action: LIGHT_ACTION_LABEL[device.last_confirmed_command.action] ?? 'Bedienopdracht',
                    time: dateTime(device.last_confirmed_command.confirmed_at),
                  }) : text('noneConfirmed')}</p>
                </div>
                <div className="grid min-w-0 content-start gap-2">
                  <DeviceTestButton device={device} disabled={!available} named />
                  <ConfirmDevice id={device.id} name={device.name} blocked={!available}
                    eligible={device.confirmation_allowed} named />
                </div>
              </li>
            ))}
          </ul>
          {list.hasNextPage && <Button disabled={!canAct || list.isFetching} onClick={() => list.fetchNextPage()}>{text('more')}</Button>}
          <div className="stack tight">
            <Button aria-disabled={!canConfirm} aria-describedby={!canConfirm ? bulkReasonId : undefined}
              onClick={() => {
                if (!canConfirm || !etag) return;
                setChecked(false);
                setBulk({ ids: selection.map((device) => device.id), names: selection.map((device) => device.name), etag });
              }}>{text('mark')}</Button>
            {!canConfirm && <p id={bulkReasonId} className="small m-0">{selected.length === 0 ? text('noSelection') : text('mutationError')}</p>}
          </div>
        </fieldset>
      )}
      {bulk && (
        <ConfirmationDialog title={text('bulkTitle')} description={text('markBody')} busy={mutation.isPending} close={() => setBulk(null)}>
          <ul className="m-0 ps-5">{bulk.names.map((name, index) => <li key={bulk.ids[index]}>{name}</li>)}</ul>
          <Acknowledgement label={text('bulkCheck')} checked={checked} setChecked={setChecked} />
          <Button variant="primair" disabled={!checked || !available || mutation.isPending}
            onClick={() => mutation.mutate(bulk, {
              onSuccess: () => {
                setNotice(text('marked', { count: bulk.ids.length }));
                setSelected([]);
                setBulk(null);
              },
              onError: () => { setBulk(null); setNeedsReload(true); },
            })}>{mutation.isPending ? text('markBusy') : bulk.ids.length === 1 ? '1 apparaat als getest markeren' : text('bulkSubmit', { count: bulk.ids.length })}</Button>
        </ConfirmationDialog>
      )}
    </section>
  );
}
