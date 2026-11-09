// Command status store (verbeterlijst 23). A command counts as done only when the server says so via
// WS `command.updated` ("confirmed"); a zone/lights update alone is never a confirmation.
//
// Usage (pages):
//   const res = await api.zoneCommand(zone.id, { target_c: 21 });
//   trackCommand(res.command_id, { key: `zone:${zone.id}`, label: 'Doeltemperatuur 21°' });
//   const cmd = useCommand(res.command_id)   // or useLatestCommand(`zone:${zone.id}`)
//   cmd?.phase -> 'sending' | 'sent' | 'confirmed' | 'failed' | ...  cmd?.text -> Dutch status line
import { useEffect, useState, useSyncExternalStore } from 'react';
import type { CommandAccepted, CommandOutcome, CommandStatus, CommandUpdate } from './api/types';

export const FINAL_STATUSES: ReadonlySet<CommandStatus> = new Set(['confirmed', 'failed', 'denied', 'cancelled']);
/** A queued command without any update for this long gets "geen reactie" (the worker may be down). */
export const QUEUED_TIMEOUT_MS = 30_000;
/** A sent command can still be confirmed on a later readback, or fail after 2 min. */
export const SENT_TIMEOUT_MS = 150_000;
const MAX_ENTRIES = 300;

export interface CommandInfo {
  id: string;
  status: CommandStatus;
  detail: string | null;
  attempts: number;
  /** A failed climate command is being retried (same id). */
  retrying: boolean;
  /** Local time of the last change. */
  updatedAt: number;
  createdAt: number;
  /** What the user asked, for messages ("Doeltemperatuur 21°"). */
  label?: string;
  /** Grouping key, e.g. "zone:<id>" or "group:<id>". */
  key?: string;
}
export type CommandPhase = 'sending' | 'retrying' | 'sent' | 'confirmed' | 'failed' | 'denied' | 'cancelled' | 'timeout';

export function isFinal(status: CommandStatus): boolean {
  return FINAL_STATUSES.has(status);
}

/** UI phase including local time-outs. */
export function commandPhase(c: CommandInfo, now: number = Date.now()): CommandPhase {
  if (c.status === 'confirmed' || c.status === 'failed' || c.status === 'denied' || c.status === 'cancelled') return c.status;
  const age = now - c.updatedAt;
  if (c.status === 'sent') return age > SENT_TIMEOUT_MS ? 'timeout' : 'sent';
  if (age > QUEUED_TIMEOUT_MS) return 'timeout';
  return c.retrying ? 'retrying' : 'sending';
}

export const COMMAND_PHASE_LABEL: Record<CommandPhase, string> = {
  sending: 'Wordt verzonden…',
  retrying: 'Mislukt, wordt opnieuw geprobeerd…',
  sent: 'Verzonden, nog niet bevestigd',
  confirmed: 'Bevestigd',
  failed: 'Mislukt',
  denied: 'Geweigerd',
  cancelled: 'Vervallen',
  timeout: 'Geen bevestiging ontvangen',
};

/** One Dutch status line, with the server's reason for failures. */
export function commandText(c: CommandInfo, now: number = Date.now()): string {
  const phase = commandPhase(c, now);
  const base = COMMAND_PHASE_LABEL[phase];
  if ((phase === 'failed' || phase === 'denied' || phase === 'cancelled' || phase === 'retrying') && c.detail) return `${base}: ${c.detail}`;
  if (phase === 'timeout') return c.status === 'sent' ? `${base}. Controleer of het apparaat het heeft uitgevoerd.` : `${base}. Werkt de achtergronddienst?`;
  return base;
}

/* ---------- store ---------- */

type Listener = () => void;
const entries = new Map<string, CommandInfo>();
const latestByKey = new Map<string, string>();
const listeners = new Set<Listener>();
let version = 0;

function emit() {
  version++;
  listeners.forEach((l) => l());
}
function prune() {
  if (entries.size <= MAX_ENTRIES) return;
  const sorted = [...entries.values()].sort((a, b) => a.updatedAt - b.updatedAt);
  for (const c of sorted.slice(0, entries.size - MAX_ENTRIES)) entries.delete(c.id);
}

export function subscribeCommands(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
export function getCommand(id: string | null | undefined): CommandInfo | null {
  return (id && entries.get(id)) || null;
}
export function latestCommandId(key: string): string | null {
  return latestByKey.get(key) ?? null;
}

/** Start tracking a command the app just sent (202 {command_id}). Safe if the WS update came first. */
export function trackCommand(id: string, meta: { key?: string; label?: string } = {}, now: number = Date.now()): CommandInfo {
  const prev = entries.get(id);
  const c: CommandInfo = prev
    ? { ...prev, label: meta.label ?? prev.label, key: meta.key ?? prev.key }
    : { id, status: 'queued', detail: null, attempts: 0, retrying: false, updatedAt: now, createdAt: now, ...meta };
  entries.set(id, c);
  if (c.key) latestByKey.set(c.key, id);
  prune();
  emit();
  return c;
}

/** Apply a WS command.updated / command.queued (also for commands this tab did not send). */
export function applyCommandUpdate(u: CommandUpdate, now: number = Date.now()) {
  const prev = entries.get(u.id);
  // Never move a final status back to a non-final one (late/duplicate events).
  if (prev && isFinal(prev.status) && !isFinal(u.status)) return;
  const c: CommandInfo = {
    id: u.id,
    status: u.status,
    detail: u.detail ?? (prev?.status === u.status ? prev.detail : null),
    attempts: u.attempts ?? prev?.attempts ?? 0,
    retrying: u.status === 'queued' ? Boolean(u.retrying) : false,
    updatedAt: now,
    createdAt: prev?.createdAt ?? now,
    label: prev?.label,
    key: prev?.key,
  };
  entries.set(u.id, c);
  prune();
  emit();
}

/** Zone.pending_command / LightGroup.last_command carry a status too (useful after a reconnect). */
export function applyCommandSnapshot(key: string, cmd: { id: string; status: CommandStatus; detail?: string | null } | CommandOutcome | null, now: number = Date.now()) {
  if (!cmd) return;
  const prev = entries.get(cmd.id);
  if (prev && prev.status === cmd.status) {
    if (!latestByKey.has(key)) latestByKey.set(key, cmd.id);
    return;
  }
  applyCommandUpdate({ id: cmd.id, status: cmd.status, detail: 'detail' in cmd ? cmd.detail : null }, now);
  const c = entries.get(cmd.id);
  if (c && !c.key) entries.set(cmd.id, { ...c, key });
  if (!latestByKey.has(key) || latestByKey.get(key) === cmd.id || !prev) latestByKey.set(key, cmd.id);
}

/** Await a 202 answer and track its command. Returns the command id. */
export async function runCommand(p: Promise<CommandAccepted>, meta: { key?: string; label?: string } = {}): Promise<string> {
  const res = await p;
  trackCommand(res.command_id, meta);
  return res.command_id;
}

/** Tests only. */
export function resetCommands() {
  entries.clear();
  latestByKey.clear();
  emit();
}

/* ---------- hooks ---------- */

function useNow(active: boolean, everyMs = 5000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [active, everyMs]);
  return now;
}

export interface CommandView extends CommandInfo {
  phase: CommandPhase;
  /** Dutch status line. */
  text: string;
  final: boolean;
  /** Still waiting for the outcome (not final, not timed out). */
  busy: boolean;
}

function view(c: CommandInfo | null, now: number): CommandView | null {
  if (!c) return null;
  const phase = commandPhase(c, now);
  return { ...c, phase, text: commandText(c, now), final: isFinal(c.status), busy: !isFinal(c.status) && phase !== 'timeout' };
}

/** Live status of one command (null until tracked or seen). Re-renders on updates and time-outs. */
export function useCommand(commandId: string | null | undefined): CommandView | null {
  useSyncExternalStore(subscribeCommands, () => version, () => version);
  const c = getCommand(commandId);
  const now = useNow(Boolean(c && !isFinal(c.status)));
  return view(c, Math.max(now, Date.now()));
}

/** Newest command for a key such as "zone:<id>" or "group:<id>". */
export function useLatestCommand(key: string | null | undefined): CommandView | null {
  useSyncExternalStore(subscribeCommands, () => version, () => version);
  return useCommand(key ? latestCommandId(key) : null);
}
