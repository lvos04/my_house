import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { api, isAccessDenied } from '../api/client';
import type { CommissioningDevice } from '../api/types';
import { qk } from '../api/keys';
import { ErrorNote, Loading } from '../components/bits';
import { Button } from '../components/ui/button';
import { dateTime } from '../format';
import { useCanAct } from '../state';
import { useCan } from '../user';
import { commissioningText as text } from './commissioning-text';

// Bij een ingetrokken leesrecht is ook de laatst bekende stand niet meer beschikbaar.
export function useAdminRead() {
  const client = useQueryClient();
  return async function readAdminData<T>(key: QueryKey, read: () => Promise<T>) {
    try {
      return await read();
    } catch (error) {
      if (isAccessDenied(error)) {
        client.setQueryData(key, null);
      }
      throw error;
    }
  };
}

export function ConfirmationDialog({ title, description, close, busy, returnFocus, children }: {
  title: string;
  description: string;
  close: () => void;
  busy: boolean;
  returnFocus?: HTMLElement | null;
  children: ReactNode;
}) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = returnFocus ?? document.activeElement;
    const element = dialog.current!;
    element.showModal();
    cancel.current?.focus();
    return () => {
      element.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  return (
    <dialog ref={dialog} role="alertdialog" aria-labelledby={id} aria-describedby={id + '-body'}
      className="card box-border max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-lg overflow-auto backdrop:bg-black/60"
      aria-busy={busy} onCancel={(event) => { event.preventDefault(); close(); }}>
      <div className="grid gap-4 break-words">
        <h2 id={id} className="m-0 text-lg">{title}</h2>
        <p id={id + '-body'} className="m-0">{description}</p>
        {busy && <p role="status" className="m-0">{text('submitted')}</p>}
        <Button ref={cancel} className="justify-self-start" onClick={close}>{busy ? text('close') : text('cancel')}</Button>
        {children}
      </div>
    </dialog>
  );
}

export function useCommissioningChange<T>(mutationFn: (value: T) => Promise<unknown>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    retry: false,
    networkMode: 'always',
    onSuccess: () => {
      for (const queryKey of [['devices'], qk.compatibility, ['zones'], ['zone'], ['overview'], ['lights']]) {
        void client.invalidateQueries({ queryKey });
      }
      return client.invalidateQueries({ queryKey: qk.commissioning });
    },
  });
}

export function ConfirmDevice({ id, name, blocked = false, eligible = true, named = false }: {
  id: string; name: string; blocked?: boolean; eligible?: boolean; named?: boolean;
}) {
  const { admin } = useCan();
  const readAdminData = useAdminRead();
  const canAct = useCanAct();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [snapshot, setSnapshot] = useState<{ data: CommissioningDevice; etag: string } | null>(null);
  const gate = useQuery({
    queryKey: qk.commissioning,
    queryFn: (ctx) => readAdminData(ctx.queryKey, api.commissioning),
    enabled: admin && open && canAct,
    staleTime: 0,
  });
  const preview = useQuery({
    queryKey: [...qk.commissioning, 'device', id],
    queryFn: (ctx) => readAdminData(ctx.queryKey, () => api.commissioningDevice(id)),
    enabled: admin && open && canAct,
    staleTime: 0,
  });
  const confirm = useCommissioningChange(({ deviceId, etag, note }: { deviceId: string; etag: string; note: string }) =>
    api.confirmDeviceTest(deviceId, etag, note),
  );
  useEffect(() => {
    if (open && !snapshot && preview.isSuccess && !preview.isFetching && preview.data?.data.id === id) {
      setSnapshot(preview.data);
    }
  }, [open, snapshot, id, preview.isSuccess, preview.isFetching, preview.data]);
  const gateKnown = gate.isSuccess && gate.data?.data.gate?.known === true;
  const visible = gate.data && preview.data && snapshot?.data.id === id ? snapshot : null;
  const changed = !!visible && (visible.etag !== preview.data?.etag || visible.data.id !== preview.data.data.id ||
    visible.data.name !== preview.data.data.name);
  const ready = admin && canAct && gateKnown && !gate.isFetching && eligible && !blocked && preview.isSuccess && !preview.isFetching &&
    !!visible && !changed && preview.data.data.confirmation_allowed === true && !confirm.isPending && !confirm.isError;
  async function refresh() {
    const result = await preview.refetch();
    if (result.isSuccess && result.data?.data.id === id) {
      setSnapshot(result.data);
      setNote('');
      confirm.reset();
    }
  }
  if (!admin) return null;
  return (
    <>
      {eligible && <Button disabled={!canAct || blocked || confirm.isPending} aria-label={named ? text('markNamed', { name }) : undefined}
        onClick={() => { setNote(''); setSnapshot(null); confirm.reset(); setOpen(true); }}>
        {text('mark')}
      </Button>}
      {open && (
        <ConfirmationDialog title={visible ? text('markTitle', { name: visible.data.name }) : text('mark')} description={text('markBody')}
          busy={confirm.isPending} close={() => setOpen(false)}>
          {!canAct && <p className="note unknown">{text('offline')}</p>}
          {gate.isSuccess && !gateKnown && <p className="note unknown">{text('unknown')}</p>}
          {gate.isError && <ErrorNote error={gate.error} />}
          {preview.isFetching && <Loading label={text('loading')} />}
          {preview.isError && <ErrorNote error={preview.error} />}
          {visible?.data.compatibility === 'tested' && (
            <p role="status">{text('tested')}{visible.data.tested_at && ` · ${dateTime(visible.data.tested_at)}`}</p>
          )}
          {visible?.data.protected && <p>{text('protected')}</p>}
          {(confirm.isError || changed) && (
            <div className="stack">
              <p role="alert" className="note warn">{changed ? 'De apparaatgegevens zijn gewijzigd. Haal de nieuwe stand op en controleer die opnieuw.' : text('mutationError')}</p>
              <Button disabled={!canAct || blocked || !eligible || preview.isFetching} onClick={refresh}>{text('retry')}</Button>
            </div>
          )}
          {preview.isSuccess && preview.data && !preview.data.data.confirmation_allowed && (
            <p role="status">Dit apparaat komt volgens de actuele teststatus niet in aanmerking voor bevestiging.</p>
          )}
          <label className="field">
            {text('note')}
            <input value={visible ? note : ''} maxLength={500} onChange={(event) => setNote(event.target.value)} disabled={!ready} />
          </label>
          <Button variant="primair" disabled={!ready}
            onClick={() => {
              if (ready && visible) confirm.mutate({ deviceId: visible.data.id, etag: visible.etag, note: note.trim() }, { onSuccess: () => setOpen(false) });
            }}>
            {confirm.isPending ? text('markBusy') : text('markSubmit')}
          </Button>
        </ConfirmationDialog>
      )}
      {confirm.isSuccess && visible && <p role="status">{visible.data.name} is als getest vastgelegd.</p>}
    </>
  );
}

export function TestHistory({ id, name }: { id: string; name: string }) {
  const { admin } = useCan();
  const readAdminData = useAdminRead();
  const [open, setOpen] = useState(false);
  const history = useInfiniteQuery({
    queryKey: qk.commissioningHistory(id),
    queryFn: (ctx) => readAdminData(ctx.queryKey, () => api.commissioningHistory(id, ctx.pageParam)),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled: admin && open,
  });
  if (!admin) return null;
  const items = history.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="min-h-11 content-center cursor-pointer">{text('history', { name })}</summary>
      {open && (
        <>
          {history.isLoading && <Loading />}
          {history.isError && (
            <div className="stack">
              <ErrorNote error={history.error} />
              <Button onClick={() => history.refetch()}>{text('retry')}</Button>
            </div>
          )}
          {history.isSuccess && items.length === 0 && <p>{text('noHistory')}</p>}
          <ol className="plain-list grid gap-4 break-words">
            {items.map((entry) => (
              <li key={entry.id}>
                <strong>{text('historyEntry', { time: dateTime(entry.at), actor: entry.actor })}</strong>
                <dl className="facts compact">
                  <div><dt>{text('model')}</dt><dd>{entry.model ?? text('notRecorded')}</dd></div>
                  <div><dt>{text('firmware')}</dt><dd>{entry.firmware ?? text('notRecorded')}</dd></div>
                </dl>
              </li>
            ))}
          </ol>
          {history.hasNextPage && <Button disabled={history.isFetching} onClick={() => history.fetchNextPage()}>{text('more')}</Button>}
        </>
      )}
    </details>
  );
}
