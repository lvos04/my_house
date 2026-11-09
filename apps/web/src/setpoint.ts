// Debounced setpoint (verbeterlijst 23): +/− presses within ~800 ms are combined and only the final
// value is sent, once. The shown value is the local draft until the command is final.
import { useCallback, useEffect, useRef, useState } from 'react';
import { useCommand, type CommandView } from './commands';

export const SETPOINT_DEBOUNCE_MS = 800;

/** Next setpoint after a +/− press, snapped to the unit's step and clamped to its range. */
export function stepSetpoint(current: number, dir: 1 | -1, step: number, min: number, max: number): number {
  const s = step > 0 ? step : 0.5;
  const k = current / s;
  const onGrid = Math.abs(k - Math.round(k)) < 1e-6;
  // Off-grid values (e.g. 20.7 with step 0.5) snap to the next step in the pressed direction.
  const next = onGrid ? (Math.round(k) + dir) * s : (dir > 0 ? Math.ceil(k) : Math.floor(k)) * s;
  return Number(Math.min(max, Math.max(min, next)).toFixed(2));
}

/** Framework-free debouncer: push() values, send() is called once with the last value after `delayMs`
 * of quiet. flush() sends now; cancel() drops the pending value. */
export class Debouncer<T> {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private value: T | undefined;
  private has = false;
  constructor(
    private send: (v: T) => void,
    private delayMs: number = SETPOINT_DEBOUNCE_MS,
  ) {}
  push(v: T) {
    this.value = v;
    this.has = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), this.delayMs);
  }
  flush() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (!this.has) return;
    this.has = false;
    this.send(this.value as T);
  }
  cancel() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.has = false;
  }
  get pending(): boolean {
    return this.has;
  }
}

export interface DebouncedSetpointOptions {
  /** The server's current target (zone.target_c). */
  value: number | null;
  min: number;
  max: number;
  step: number;
  /** Send the final value; return the command id (from the 202 answer) so its outcome is tracked. */
  onSend: (target: number) => Promise<string | void>;
  delayMs?: number;
  /** Starting point when value is null (e.g. the unit is off). */
  fallback?: number;
}
export interface DebouncedSetpoint {
  /** What to show: the local draft while waiting, else the server value. */
  value: number | null;
  /** A press is waiting for the quiet period (nothing sent yet). */
  waiting: boolean;
  /** The command of the last sent value (status via useCommand). */
  command: CommandView | null;
  /** Error from onSend (e.g. ApiError 409 mode_required). Cleared on the next press. */
  error: unknown;
  bump: (dir: 1 | -1) => void;
  setValue: (v: number) => void;
  flush: () => void;
  reset: () => void;
}

/** Hook for +/− controls: combine presses, send one final value, keep showing it until the command
 * is confirmed (or revert when it fails/is refused). */
export function useDebouncedSetpoint(o: DebouncedSetpointOptions): DebouncedSetpoint {
  const [draft, setDraft] = useState<number | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [commandId, setCommandId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const optsRef = useRef(o);
  optsRef.current = o;
  const draftRef = useRef<number | null>(null);
  draftRef.current = draft;

  const debouncer = useRef<Debouncer<number> | null>(null);
  if (!debouncer.current) {
    debouncer.current = new Debouncer<number>((v) => {
      setWaiting(false);
      optsRef.current.onSend(v).then(
        (id) => setCommandId(id || null),
        (e) => {
          setError(e);
          setDraft(null);
        },
      );
    }, o.delayMs ?? SETPOINT_DEBOUNCE_MS);
  }
  useEffect(() => () => debouncer.current?.flush(), []);

  const command = useCommand(commandId);
  // Final outcome: confirmed -> the server value takes over once it matches; failed -> revert.
  useEffect(() => {
    if (!command || waiting) return;
    if (command.status === 'confirmed' && (o.value === draft || draft === null)) setDraft(null);
    else if (command.status === 'confirmed') {
      const t = setTimeout(() => setDraft(null), 5000); // zone.updated normally follows at once
      return () => clearTimeout(t);
    }
    if (command.status === 'failed' || command.status === 'denied' || command.status === 'cancelled' || command.phase === 'timeout') setDraft(null);
  }, [command, waiting, o.value, draft]);

  const setValue = useCallback((v: number) => {
    const { min, max } = optsRef.current;
    const c = Math.min(max, Math.max(min, v));
    setError(null);
    setDraft(c);
    setWaiting(true);
    debouncer.current!.push(c);
  }, []);
  const bump = useCallback(
    (dir: 1 | -1) => {
      const { value, fallback, step, min, max } = optsRef.current;
      const base = draftRef.current ?? value ?? fallback ?? min;
      setValue(stepSetpoint(base, dir, step, min, max));
    },
    [setValue],
  );
  const flush = useCallback(() => debouncer.current!.flush(), []);
  const reset = useCallback(() => {
    debouncer.current!.cancel();
    setWaiting(false);
    setDraft(null);
    setError(null);
  }, []);

  return { value: draft ?? o.value, waiting, command, error, bump, setValue, flush, reset };
}
