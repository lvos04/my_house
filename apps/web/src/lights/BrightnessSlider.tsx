import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';

/** Keys that change a range input's value. Tab, Shift etc. never commit (no command on focus). */
const VALUE_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown']);
export const KEY_COMMIT_DELAY_MS = 400;

/**
 * Brightness slider that sends only on commit: when the pointer is released, or shortly after the last
 * value key is released (so holding or tapping an arrow key sends one final value). Never while
 * dragging, never on focus, never when the value did not change (verbeterlijst kleine punten / 27).
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
  const dragging = useRef(false);
  const lastSent = useRef<number | null>(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    onCommit(v);
  };
  const onPointerUp = (e: PointerEvent<HTMLInputElement>) => {
    dragging.current = false;
    commit(Number(e.currentTarget.value));
  };
  const onKeyUp = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!VALUE_KEYS.has(e.key)) return;
    const v = Number(e.currentTarget.value);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => commit(v), keyDelayMs);
  };

  return (
    <div className="field lt-slider">
      <span className="row" style={{ justifyContent: 'space-between' }}>
        <label htmlFor={id}>{label}</label>
        <span className="muted" aria-hidden="true">{draft}%</span>
      </span>
      <input
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
        onKeyUp={onKeyUp}
        onBlur={(e) => {
          if (timer.current) commit(Number(e.currentTarget.value));
        }}
      />
    </div>
  );
}
