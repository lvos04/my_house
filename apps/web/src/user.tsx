import { createContext, useContext } from 'react';
import type { User } from './api/types';
import { useConnectionOptional } from './state';

/** The logged-in user: /auth/status, enriched with /auth/me (is_admin, can_control, …) by App. */
export const UserContext = createContext<User | null>(null);
export const useUser = () => useContext(UserContext);

export interface Can {
  /** Manage things: settings, devices, users, schedules (web admin or phone with role admin). */
  admin: boolean;
  /** Operate climate and lights (admin, or phone role control). Viewers never see controls. */
  control: boolean;
  /** Read-only account or phone. */
  viewer: boolean;
}

/** What the current user may do, from /auth/me (can_control/is_admin) or the WS hello, else the role.
 * Hide controls when `control` is false; hide management when `admin` is false. Offline state is
 * separate: combine with useCanAct() to disable buttons while offline. */
export function useCan(): Can {
  const user = useUser();
  const hello = useConnectionOptional()?.hello ?? null;
  const roleAdmin = user?.role === 'admin';
  const admin = user?.is_admin ?? roleAdmin;
  const control = user?.can_control ?? hello?.can_control ?? roleAdmin;
  return { admin: Boolean(user) && admin, control: Boolean(user) && control, viewer: !control };
}

export const useIsAdmin = () => useCan().admin;
