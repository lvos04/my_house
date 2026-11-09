import type { RelayDetails } from '../api/types';
import { StateLabel } from '../components/bits';

/** Relay and "Switch lock" of an Energy Socket, read-only (verbeterlijst 15). The app never switches a
 * protected socket; it only shows the state and how to fix it in the HomeWizard Energy app. */
export function RelayStatus({ relay, protectedDevice }: { relay: RelayDetails; protectedDevice: boolean }) {
  if (relay.state === 'unsupported') {
    return (
      <div className="dv-relay" data-testid="relay">
        <StateLabel state="unknown">Stand van de stekker is niet uit te lezen</StateLabel>
      </div>
    );
  }
  const on = relay.on;
  const lock = relay.switch_lock;
  return (
    <div className="dv-relay stack tight" data-testid="relay">
      <StateLabel state={on === true ? 'ok' : on === false ? 'error' : 'unknown'}>
        {on === true ? 'Stekker staat aan' : on === false ? 'Stekker staat UIT' : 'Stand van de stekker onbekend'}
      </StateLabel>
      {on === false && (
        <p className="note error" style={{ margin: 0 }}>
          {protectedDevice ? 'De boiler krijgt geen stroom. ' : ''}
          Zet de stekker weer aan met de knop op de stekker zelf of in de HomeWizard Energy-app. Deze app schakelt hem bewust niet.
        </p>
      )}
      <StateLabel state={lock === true ? 'ok' : lock === false ? 'warn' : 'unknown'}>
        {lock === true ? 'Switch lock staat aan' : lock === false ? 'Switch lock staat uit' : 'Switch lock onbekend'}
      </StateLabel>
      {lock === false && (
        <p className="note warn" style={{ margin: 0 }}>
          Met ‘Switch lock’ aan blijft de stekker altijd aan: de knop op de stekker en de app kunnen hem dan niet meer uitzetten.
          Zet het aan in de HomeWizard Energy-app: kies deze Energy Socket en zet bij de instellingen van de stekker ‘Switch lock’ aan.
          (De precieze plek kan per app-versie iets verschillen.)
        </p>
      )}
    </div>
  );
}
