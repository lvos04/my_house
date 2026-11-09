import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react';

export interface TabItem<K extends string = string> {
  key: K;
  label: ReactNode;
  disabled?: boolean;
}

/**
 * Accessible tabs (WAI-ARIA tabs pattern): role tablist/tab/tabpanel, roving tabindex, ←/→ (and ↑/↓),
 * Home/End move between tabs and activate them. Controlled: pass `value` and `onChange`.
 *
 *   <Tabs label="Periode" items={[{key:'day',label:'Dag'}, …]} value={p} onChange={setP}>
 *     {(key) => <Panel for={key} />}
 *   </Tabs>
 *
 * Without children it renders only the tablist, for content that lives elsewhere on the page (e.g. the
 * energy period switch). Then pass `hasPanel` and the same `idBase`, and wrap that content in
 * `<TabPanel idBase={…} value={key}>`: the selected tab gets aria-controls, the panel role=tabpanel and
 * aria-labelledby (a tablist with no panel is not a tab set).
 */
export function Tabs<K extends string>({
  label,
  items,
  value,
  onChange,
  children,
  className = 'tabs',
  idBase,
  hasPanel = false,
}: {
  label: string;
  items: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  children?: (key: K) => ReactNode;
  className?: string;
  idBase?: string;
  /** No children: the caller renders the panel itself with <TabPanel> (same idBase). */
  hasPanel?: boolean;
}) {
  const autoId = useId();
  const base = idBase ?? autoId;
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const enabled = items.filter((i) => !i.disabled);

  const move = (e: KeyboardEvent<HTMLButtonElement>) => {
    const idx = enabled.findIndex((i) => i.key === value);
    let next = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (idx + 1) % enabled.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (idx - 1 + enabled.length) % enabled.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = enabled.length - 1;
    if (next < 0) return;
    e.preventDefault();
    const k = enabled[next].key;
    onChange(k);
    refs.current[k]?.focus();
  };

  return (
    <>
      <div role="tablist" aria-label={label} className={className}>
        {items.map((i) => {
          const selected = i.key === value;
          return (
            <button
              key={i.key}
              ref={(el) => {
                refs.current[i.key] = el;
              }}
              type="button"
              role="tab"
              id={tabId(base, i.key)}
              aria-selected={selected}
              aria-controls={(children || hasPanel) && selected ? panelId(base, i.key) : undefined}
              tabIndex={selected ? 0 : -1}
              disabled={i.disabled}
              onClick={() => onChange(i.key)}
              onKeyDown={move}
            >
              {i.label}
            </button>
          );
        })}
      </div>
      {children && (
        <div role="tabpanel" id={panelId(base, value)} aria-labelledby={tabId(base, value)} tabIndex={0} className="tabpanel">
          {children(value)}
        </div>
      )}
    </>
  );
}

/**
 * The panel of a `<Tabs hasPanel idBase={idBase}>` rendered elsewhere: the content for tab `value`.
 * `focusable` (default) puts the panel in the tab order, as the pattern asks when its content does not
 * start with something focusable; pass false when it starts with a control (e.g. another tablist).
 */
export function TabPanel({
  idBase,
  value,
  focusable = true,
  className,
  children,
}: {
  idBase: string;
  value: string;
  focusable?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div role="tabpanel" id={panelId(idBase, value)} aria-labelledby={tabId(idBase, value)} tabIndex={focusable ? 0 : undefined} className={className}>
      {children}
    </div>
  );
}

export const tabId = (base: string, key: string) => `${base}-tab-${key}`;
export const panelId = (base: string, key: string) => `${base}-panel-${key}`;
