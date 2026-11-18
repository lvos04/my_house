import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import { ErrorNote, Loading } from '../components/bits';
import { Button } from '../components/ui/button';
import { dateTime } from '../format';
import { useCanAct } from '../state';
import { useCan } from '../user';
import { commissioningText as text } from './commissioning-text';

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
      onCancel={(event) => { event.preventDefault(); if (!busy) close(); }}>
      <div className="grid gap-4 break-words">
        <h2 id={id} className="m-0 text-lg">{title}</h2>
        <p id={id + '-body'} className="m-0">{description}</p>
        <Button ref={cancel} className="justify-self-start" onClick={close} disabled={busy}>{text('cancel')}</Button>
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
    onSuccess: () => Promise.all([
      client.invalidateQueries({ queryKey: qk.commissioning }),
      client.invalidateQueries({ queryKey: ['devices'] }),
      client.invalidateQueries({ queryKey: qk.compatibility }),
      client.invalidateQueries({ queryKey: ['zones'] }),
      client.invalidateQueries({ queryKey: ['zone'] }),
      client.invalidateQueries({ queryKey: ['overview'] }),
      client.invalidateQueries({ queryKey: ['lights'] }),
    ]),
  });
}

export function ConfirmDevice({ id, name, blocked = false, eligible = true, named = false }: {
  id: string; name: string; blocked?: boolean; eligible?: boolean; named?: boolean;
}) {
  const { admin } = useCan();
  const canAct = useCanAct();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const preview = useQuery({
    queryKey: [...qk.commissioning, 'device', id],
    queryFn: () => api.commissioningDevice(id),
    enabled: admin && open && canAct,
  });
  const confirm = useCommissioningChange(({ etag, note }: { etag: string; note: string }) =>
    api.confirmDeviceTest(id, etag, note),
  );
  if (!admin) return null;
  return (
    <>
      {eligible && <Button disabled={!canAct || blocked} onClick={() => { setNote(''); confirm.reset(); setOpen(true); }}>
        {named ? text('markNamed', { name }) : text('mark')}
      </Button>}
      {open && (
        <ConfirmationDialog title={text('markTitle', { name })} description={text('markBody')}
          busy={confirm.isPending} close={() => setOpen(false)}>
          {!canAct && <p className="note unknown">{text('offline')}</p>}
          {preview.isFetching && <Loading label={text('loading')} />}
          {preview.isError && <ErrorNote error={preview.error} />}
          {confirm.isError && (
            <div className="stack">
              <p role="alert" className="note error">{text('mutationError')}</p>
              <Button disabled={!canAct || preview.isFetching} onClick={async () => {
                const refreshed = await preview.refetch();
                if (refreshed.isSuccess) { setNote(''); confirm.reset(); }
              }}>{text('retry')}</Button>
            </div>
          )}
          {preview.isSuccess && !preview.data.data.confirmation_allowed && (
            <p role="status">Dit apparaat komt volgens de actuele teststatus niet in aanmerking voor bevestiging.</p>
          )}
          <label className="field">
            {text('note')}
            <input value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} disabled={confirm.isPending} />
          </label>
          <Button variant="primair" disabled={!canAct || confirm.isPending || confirm.isError || preview.isFetching ||
            !preview.isSuccess || !preview.data.data.confirmation_allowed}
            onClick={() => {
              if (preview.data) confirm.mutate({ etag: preview.data.etag, note: note.trim() }, { onSuccess: () => setOpen(false) });
            }}>
            {confirm.isPending ? text('markBusy') : text('markSubmit')}
          </Button>
        </ConfirmationDialog>
      )}
      {confirm.isSuccess && <p role="status">{name} is als getest vastgelegd.</p>}
    </>
  );
}

export function TestHistory({ id, name }: { id: string; name: string }) {
  const { admin } = useCan();
  const [open, setOpen] = useState(false);
  const history = useInfiniteQuery({
    queryKey: qk.commissioningHistory(id),
    queryFn: ({ pageParam }) => api.commissioningHistory(id, pageParam),
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
