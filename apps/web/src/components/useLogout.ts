import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import { clearSnapshots } from '../snapshot';

/** Log out: ends the session on the server, forgets cached data and the offline snapshot. */
export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      clearSnapshots();
      qc.clear();
      qc.setQueryData(qk.auth, { setup_required: false, user: null });
    },
  });
}
