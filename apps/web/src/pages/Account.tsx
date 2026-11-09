import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Tabs, type TabItem } from '../components/Tabs';
import { useCan } from '../user';
import { OwnPanel } from './account/Own';
import { UsersPanel } from './account/Users';
import { PhonesPanel } from './account/Phones';
import { AuditPanel } from './account/Audit';

type AccountTab = 'mijn' | 'gebruikers' | 'telefoons' | 'logboek';
const ADMIN_TABS: TabItem<AccountTab>[] = [
  { key: 'mijn', label: 'Mijn account' },
  { key: 'gebruikers', label: 'Gebruikers' },
  { key: 'telefoons', label: 'Telefoons' },
  { key: 'logboek', label: 'Logboek' },
];
const OWN_TABS = ADMIN_TABS.slice(0, 1);

/** /account: own password for everyone; users, phones and the audit log for admins (verbeterlijst 18). */
export function AccountPage() {
  const can = useCan();
  const location = useLocation();
  const navigate = useNavigate();
  const items = can.admin ? ADMIN_TABS : OWN_TABS;
  const fromHash = location.hash.slice(1) as AccountTab;
  const [tab, setTab] = useState<AccountTab>(items.some((i) => i.key === fromHash) ? fromHash : 'mijn');
  useEffect(() => {
    if (items.some((i) => i.key === fromHash)) setTab(fromHash);
  }, [fromHash, items]);
  const change = (k: AccountTab) => {
    setTab(k);
    navigate({ hash: k === 'mijn' ? '' : k }, { replace: true });
  };
  return (
    <>
      <header className="page-head">
        <div><span className="eyebrow">Toegang</span><h1 tabIndex={-1}>Account</h1></div>
      </header>
      <section className="card">
        {items.length > 1 ? (
          <Tabs label="Accountonderdelen" items={items} value={tab} onChange={change} idBase="account">
            {(k) => (k === 'gebruikers' ? <UsersPanel /> : k === 'telefoons' ? <PhonesPanel /> : k === 'logboek' ? <AuditPanel /> : <OwnPanel />)}
          </Tabs>
        ) : (
          <OwnPanel />
        )}
      </section>
    </>
  );
}
