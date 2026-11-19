import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api } from '../../api/client';
import { qk } from '../../api/keys';
import type { HubPinCandidate, HubPinInput } from '../../api/types';
import { ActionButton, Loading } from '../../components/bits';
import { useCanAct } from '../../state';

const fingerprintError = 'De vingerafdruk komt niet overeen. Neem de vingerafdruk uit de Hub over.';

function normalizedFingerprint(value: string) {
  if (/[^ -~]/.test(value)) return '';
  return value.toUpperCase().replace(/[ -]/g, '').replace(/O/g, '0').replace(/[IL]/g, '1');
}

export function HubKeysSection() {
  const queryClient = useQueryClient();
  const [denied, setDenied] = useState(false);

  function closeCard() {
    setDenied(true);
    queryClient.removeQueries({ queryKey: qk.hubPin, exact: true });
    void queryClient.invalidateQueries({ queryKey: qk.me, exact: true });
    void queryClient.invalidateQueries({ queryKey: qk.auth, exact: true });
  }

  return denied ? null : <HubKeysCard onAccessDenied={closeCard} />;
}

function HubKeysCard({ onAccessDenied }: { onAccessDenied: () => void }) {
  const queryClient = useQueryClient();
  const active = useRef(false);
  function revokeAccess() {
    if (!active.current) return;
    active.current = false;
    onAccessDenied();
  }

  const status = useQuery({
    queryKey: qk.hubPin, queryFn: ({ signal }) => api.hubPin(revokeAccess, signal),
    gcTime: 0, refetchOnWindowFocus: false, retry: false,
  });
  const canAct = useCanAct();
  const [mode, setMode] = useState<HubPinInput['mode'] | null>(null);
  const [source, setSource] = useState('');
  const [fingerprint, setFingerprint] = useState('');
  const [candidate, setCandidate] = useState<(HubPinCandidate & { etag: string }) | null>(null);
  const [busy, setBusy] = useState(false);
  const [fieldError, setFieldError] = useState<'source' | 'fingerprint' | null>(null);
  const [problem, setProblem] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const sourceField = useRef<HTMLTextAreaElement>(null);
  const fingerprintField = useRef<HTMLInputElement>(null);
  const pin = status.isError ? undefined : status.data?.data;
  const formMode = mode ?? (pin?.state === 'empty' ? 'paste' : null);

  // Systeem verwijdert de kaart als de rol wijzigt. Een lopend antwoord mag de pinstatus dan niet terugzetten.
  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  // De eerste laadactie laat de focus staan. Een bewust geopend formulier begint bij het plakveld.
  useEffect(() => {
    if (mode) sourceField.current?.focus();
  }, [mode]);
  useEffect(() => {
    if (candidate) fingerprintField.current?.focus();
  }, [candidate]);
  useEffect(() => {
    if (fieldError === 'source') sourceField.current?.focus();
    if (fieldError === 'fingerprint') fingerprintField.current?.focus();
  }, [fieldError]);

  function reset() {
    setMode(null);
    setSource('');
    setFingerprint('');
    setCandidate(null);
    setFieldError(null);
    setProblem('');
    heading.current?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active.current || !canAct || busy || !formMode || !status.data || !source.trim()) return;
    if (candidate && normalizedFingerprint(fingerprint) !== normalizedFingerprint(candidate.fingerprint)) {
      setFieldError('fingerprint');
      setProblem(fingerprintError);
      fingerprintField.current?.focus();
      return;
    }
    if (!candidate && source.length > 131_072) {
      setFieldError('source');
      setProblem('De geplakte tekst is te lang. Kopieer alleen de publieke sleutelset of de rotatieverklaringen.');
      return;
    }
    setBusy(true);
    setFieldError(null);
    setProblem('');
    const input = { mode: formMode, source };
    try {
      if (candidate) {
        const result = await api.confirmHubPin({ ...input, fingerprint }, candidate.etag, revokeAccess);
        if (!active.current) return;
        queryClient.setQueryData(qk.hubPin, result);
        reset();
      } else {
        const { etag } = status.data;
        const result = await api.verifyHubPin(input, etag, revokeAccess);
        if (active.current) setCandidate({ ...result, etag });
      }
    } catch (error) {
      if (!active.current) return;
      if (error instanceof ApiError && (error.status === 412 ||
        ['sleutelset_gewijzigd', 'sleutelset_herstel_gewijzigd'].includes(error.code))) {
        reset();
        await status.refetch();
        if (!active.current) return;
      }
      if (error instanceof ApiError && error.code === 'vingerafdruk_verschilt') {
        setFieldError('fingerprint');
        setProblem(fingerprintError);
      } else {
        if (error instanceof ApiError && ['sleutelset_ongeldig', 'sleutelrotatie_ongeldig', 'rotatievolgorde_ouder'].includes(error.code)) {
          setFieldError('source');
        }
        setProblem(error instanceof ApiError ? error.message : 'De sleutelset kon niet worden gecontroleerd of vastgelegd. Probeer opnieuw.');
      }
    } finally {
      if (active.current) setBusy(false);
    }
  }

  const sourceLabel = formMode === 'rotation' ? 'Ondertekende rotatieverklaringen' : 'Publieke sleutelset uit de Hub';

  return (
    <section aria-labelledby="hub-keys-title" className="card">
      <h2 id="hub-keys-title" ref={heading} tabIndex={-1}>Koppeling met de Hub (bediening)</h2>
      <p>De Hub mag lampen en klimaat bedienen met jouw goedkeuring in de Hub. De Hub mag de boiler nooit bedienen.</p>

      {status.isPending && <Loading label="Hub-koppeling laden…" />}
      {status.isError && (
        <div className="stack">
          <p className="note warn" role="status">De status van de Hub-koppeling is niet beschikbaar.</p>
          <div><ActionButton className="btn" onClick={() => void status.refetch()}>Opnieuw laden</ActionButton></div>
        </div>
      )}
      {pin?.state === 'empty' && <p className="muted" role="status">Er is nog geen Hub-sleutelset vastgelegd.</p>}
      {pin?.state === 'damaged' && (
        <p className="note warn" role="status">
          De opgeslagen sleutelset is beschadigd. De Hub kan pas weer bedienen nadat je de actuele sleutelset hebt bevestigd.
        </p>
      )}
      {pin?.state === 'pinned' && (
        <div role="status">
          <dl className="facts">
            <div><dt>{candidate ? 'Oude vingerafdruk' : 'Vastgelegde vingerafdruk'}</dt><dd className="break">{pin.fingerprint}</dd></div>
            <div><dt>Vastgelegde rotatievolgorde</dt><dd>{pin.sequence}</dd></div>
          </dl>
        </div>
      )}
      {!formMode && !status.isError && pin?.state === 'pinned' && (
        <div className="row">
          <ActionButton className="btn" onClick={() => setMode('rotation')}>Sleutelrotatie controleren</ActionButton>
          <ActionButton className="btn" onClick={() => setMode('paste')}>Opnieuw koppelen</ActionButton>
        </div>
      )}
      {!formMode && !status.isError && pin?.state === 'damaged' && (
        <div><ActionButton className="btn" onClick={() => setMode('recovery')}>Sleutelset herstellen</ActionButton></div>
      )}

      {formMode && !status.isError && (
        <form className="stack" aria-label="Hub-sleutels controleren en vastleggen" noValidate onSubmit={submit}>
          <p>
            {formMode === 'rotation'
              ? 'Plak de ondertekende rotatieverklaringen van de Hub. Heb je alleen een publieke sleutelset, kies dan Opnieuw koppelen.'
              : 'Open in de Hub Instellingen → Diensten en kopieer de publieke sleutelset van de Hub waarmee je wilt koppelen.'}
          </p>
          {formMode === 'recovery' && (
            <p>Voor herstel moet de geplakte sleutelset ook de actuele rotatievolgorde uit de Hub bevatten.</p>
          )}
          <label className="field" htmlFor="hub-keys-source">
            {sourceLabel} (verplicht)
            <textarea id="hub-keys-source" ref={sourceField} value={source} readOnly={busy} required autoComplete="off" spellCheck={false}
              aria-invalid={fieldError === 'source' || undefined}
              aria-describedby={fieldError === 'source' ? 'hub-keys-problem' : undefined}
              onChange={(event) => {
                setSource(event.target.value);
                setCandidate(null);
                setFingerprint('');
                setFieldError(null);
                setProblem('');
              }} />
          </label>
          {fieldError === 'source' && <p id="hub-keys-problem" className="error-text" role="alert">{problem}</p>}
          {candidate && (
            <>
              <div role="status">
                {pin?.state === 'pinned' && <p className="note info">Rotatie wacht op bevestiging.</p>}
                <dl className="facts">
                  <div><dt>{pin?.state === 'pinned' ? 'Nieuwe vingerafdruk' : 'Berekende vingerafdruk'}</dt><dd className="break">{candidate.fingerprint}</dd></div>
                  <div><dt>Rotatievolgorde</dt><dd>{candidate.sequence}</dd></div>
                </dl>
              </div>
              <label className="field" htmlFor="hub-keys-fingerprint">
                Vingerafdruk uit de Hub (verplicht)
                <input id="hub-keys-fingerprint" ref={fingerprintField} value={fingerprint} readOnly={busy} required
                  autoComplete="off" spellCheck={false} aria-invalid={fieldError === 'fingerprint' || undefined}
                  aria-describedby={fieldError === 'fingerprint' ? 'hub-keys-problem' : undefined}
                  onChange={(event) => { setFingerprint(event.target.value); setFieldError(null); setProblem(''); }} />
              </label>
              {fieldError === 'fingerprint' && <p id="hub-keys-problem" className="error-text" role="alert">{problem}</p>}
            </>
          )}
          <div className="row">
            <ActionButton className="btn primary" type="submit" disabled={busy || !source.trim() || Boolean(candidate && !fingerprint.trim())}>
              {candidate ? 'Vastleggen' : 'Controleren'}
            </ActionButton>
            {(mode || source) && <button className="btn" type="button" disabled={busy} onClick={reset}>Annuleren</button>}
          </div>
        </form>
      )}
      {busy && <Loading label={candidate ? 'Sleutelset vastleggen…' : 'Sleutelset controleren…'} />}
      {problem && fieldError === null && <p className="note error" role="alert">{problem}</p>}
      {pin?.state === 'pinned' && !formMode && (
        <p>Gebruik de vastgelegde vingerafdruk en rotatievolgorde om deze app in de Hub te bevestigen.</p>
      )}
    </section>
  );
}
