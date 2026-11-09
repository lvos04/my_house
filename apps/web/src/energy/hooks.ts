import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import { browserTimeZone } from '../format';
import { todayIn } from './period';

/** The selected home's IANA time zone; null while the homes list is still loading. Falls back to the
 * browser's zone only when the home is unknown (never silently while loading). */
export function useHomeTimezone(homeId: string | null): string | null {
  const homes = useQuery({ queryKey: qk.homes, queryFn: () => api.homes(), staleTime: 5 * 60_000 });
  const list = Array.isArray(homes.data) ? homes.data : [];
  const tz = list.find((h) => h.id === homeId)?.timezone;
  if (tz) return tz;
  if (homes.isLoading) return null;
  return browserTimeZone();
}

/** Today's date in `tz`, re-evaluated every minute so the page rolls over at the home's midnight. */
export function useToday(tz: string | null, everyMs = 60_000): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return tz ? todayIn(tz, new Date(now)) : null;
}
