import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

/** Keys that change a range input's value. Tab, Shift etc. never commit (no command on focus). */
const VALUE_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown']);
export const KEY_COMMIT_DELAY_MS = 400;

/**
 * Brightness slider that sends only on commit: when the pointer is released, or shortly after the last
 * value key is released (so holding or tapping an arrow key sends one final value). Never while
 * dragging, never on focus, never when the value did not change (verbeterlijst kleine punten / 27).
 *
 * Assistive technology (VoiceOver/TalkBack swiping up or down on the slider) changes the value without
 * any pointer or key event, only `input` and a native `change`. A native `change` that no pointer or
 * value key came before therefore commits too, after the same short delay, so a few quick swipes
 * still send one final value (verbeterlijst kleine punten, toegankelijkheid web).
 */
export function BrightnessSlider({
  value,
  label,
  disabled,
  onCommit,
  keyDelayMs = KEY_COMMIT_DELAY_MS,
}: {
  value: number | null;
  label: string;
  disabled?: boolean;
  onCommit: (v: number) => void;
  keyDelayMs?: number;
}) {
  const id = useId();
  const [draft, setDraft] = useState(value ?? 100);
  const input = useRef<HTMLInputElement>(null);
  const dragging = useRef(false);
  /** A value key is down: its keyup commits, the `change` events it causes do not. */
  const keyDown = useRef(false);
  const lastSent = useRef<number | null>(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef({ onCommit, keyDelayMs });
  latest.current = { onCommit, keyDelayMs };

  // Follow the server value unless the user is busy with the slider.
  useEffect(() => {
    lastSent.current = value;
    if (!dragging.current && !timer.current) setDraft(value ?? 100);
  }, [value]);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const commit = (v: number) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    if (Number.isNaN(v) || v === lastSent.current) return;
    lastSent.current = v;
    latest.current.onCommit(v);
  };
  /** Commit `v` after a short pause; a newer value within the pause replaces it. */
  const commitSoon = (v: number) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => commit(v), latest.current.keyDelayMs);
  };
  const onPointerUp = (e: PointerEvent<HTMLInputElement>) => {
    dragging.current = false;
    commit(Number(e.currentTarget.value));
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (VALUE_KEYS.has(e.key)) keyDown.current = true;
  };
  const onKeyUp = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!VALUE_KEYS.has(e.key)) return;
    keyDown.current = false;
    commitSoon(Number(e.currentTarget.value));
  };
  const onNativeChange = useRef<() => void>(() => undefined);
  onNativeChange.current = () => {
    // Pointer and keys commit themselves (pointerup / keyup); this is for assistive technology.
    if (dragging.current || keyDown.current || !input.current) return;
    const v = Number(input.current.value);
    if (v === lastSent.current && !timer.current) return; // e.g. the change that follows a pointerup
    commitSoon(v);
  };
  // React's onChange is the `input` event; the native `change` (the user's committed value) needs its own listener.
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    const listener = () => onNativeChange.current();
    el.addEventListener('change', listener);
    return () => el.removeEventListener('change', listener);
  }, []);

  return (
    <div className="field lt-slider">
      <span className="row" style={{ justifyContent: 'space-between' }}>
        <label htmlFor={id}>{label}</label>
        <span className="muted" aria-hidden="true">{draft}%</span>
      </span>
      <input
        ref={input}
        id={id}
        type="range"
        min={1}
        max={100}
        step={1}
        value={draft}
        disabled={disabled}
        aria-valuetext={`${draft}%`}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerDown={() => {
          dragging.current = true;
        }}
        onPointerUp={onPointerUp}
        onPointerCancel={() => {
          dragging.current = false;
        }}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
        onBlur={(e) => {
          keyDown.current = false;
          if (timer.current) commit(Number(e.currentTarget.value));
        }}
      />
    </div>
  );
}
