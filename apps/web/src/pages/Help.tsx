import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { qk } from '../api/keys';
import type { CompatibilityRow } from '../api/types';
import { ErrorNote, Loading } from '../components/bits';
import { ProviderInstructions } from '../components/setup/ProviderPair';
import { COMPATIBILITY_LABEL, PROVIDER_LABEL, t } from '../i18n';
import { useCan } from '../user';

interface ProviderHelp {
  provider: string;
  intro: string;
  local: string[];
  vendorFirst: string[];
  steps: { text: string; photo?: string }[];
  problems: [string, string][];
}

/** Short help per manufacturer. Photo slots are text placeholders until real photos are added. */
export const PROVIDER_HELP: ProviderHelp[] = [
  {
    provider: 'hue',
    intro: 'De Hue Bridge (de ronde of vierkante doos bij je router) bedient de lampen. De app praat lokaal met de bridge; internet is niet nodig.',
    local: ['Lampen en lichtgroepen aan/uit en dimmen', 'Scènes kiezen', 'Hoeveel lampen aan staan en of ze bereikbaar zijn', 'Knoppen van Hue-schakelaars aan app-acties koppelen'],
    vendorFirst: ['Lampen, kamers en schakelaars voeg je eerst toe in de Hue-app.', 'Scènes maak je in de Hue-app; ze verschijnen dan vanzelf hier.'],
    steps: [
      { text: 'Geef de Hue Bridge een vast IP-adres in je router (DHCP-reservering).', photo: 'Routerinstelling met een vast adres voor de Hue Bridge' },
      { text: 'Vul het IP-adres in bij Apparaten of in de installatiewizard en kies Koppelen.' },
      { text: 'Druk binnen 30 seconden op de grote ronde knop bovenop de bridge en kies Opnieuw proberen.', photo: 'Hue Bridge met de link-knop bovenop' },
    ],
    problems: [
      ['“Druk op de link-knop”', 'Druk op de knop van de bridge en probeer binnen 30 seconden opnieuw.'],
      ['Knoppen doen niets', 'Kijk op Systeem bij de Hue-koppeling of de gebeurtenissen verbonden zijn.'],
    ],
  },
  {
    provider: 'homewizard',
    intro: 'HomeWizard-meters meten stroom (P1-meter in de slimme meter, kWh-meters, Energy Sockets) en eventueel gas. De app leest ze lokaal uit.',
    local: ['Vermogen nu en meterstanden (ook per tarief en teruglevering)', 'Gas via de P1-meter', 'Stand van een Energy Socket (alleen uitlezen; de boilerstekker wordt nooit geschakeld)'],
    vendorFirst: ['Koppel de meter eerst aan je wifi met de HomeWizard Energy-app.', 'Zet in die app per apparaat “Lokale API” aan (Instellingen › Meters › het apparaat).'],
    steps: [
      { text: 'Zet in de HomeWizard Energy-app “Lokale API” aan.', photo: 'HomeWizard Energy-app met de schakelaar Lokale API' },
      { text: 'Geef de meter een vast IP-adres in je router.' },
      { text: 'Vul het IP-adres in en kies Koppelen. Nieuwere meters vragen soms om een druk op de knop van het apparaat.', photo: 'P1-meter met de knop aan de zijkant' },
      { text: 'Wijs bij Meters en rollen de P1-meter toe als “Huis totaal” en de kWh-meter als “Airco totaal”.' },
    ],
    problems: [
      ['“Ander apparaat op dit adres”', 'Het IP-adres is veranderd. Pas het adres aan bij Apparaten en geef de meter een vast adres in de router.'],
      ['Geen gas zichtbaar', 'Gas komt alleen via de P1-meter, en alleen als je slimme meter gas doorgeeft.'],
    ],
  },
  {
    provider: 'gree',
    intro: 'Gree-airco’s met een wifi-module kunnen lokaal bediend worden (aan/uit, modus, temperatuur). Niet elk model of elke module ondersteunt dit; daarom eerst de stappen van de fabrikant.',
    local: ['Aan/uit, modus en doeltemperatuur (hele graden)', 'Ventilator en swing, als de unit ze heeft', 'De temperatuur van de unit zelf, als die een sensor heeft'],
    vendorFirst: ['Koppel elke binnenunit eerst met de Gree+-app aan je 2,4 GHz-wifi.', 'Laat de app daarna gerust staan; voor de lokale bediening is hij niet meer nodig.'],
    steps: [
      { text: 'Koppel de unit met de Gree+-app aan het 2,4 GHz-wifi-netwerk.', photo: 'Gree+-app tijdens het toevoegen van een unit' },
      { text: 'Zoek in je router het IP-adres van de unit en maak het vast.' },
      { text: 'Vink in de wizard aan dat de unit gekoppeld is, vul het adres in en kies Koppelen.' },
      { text: 'Lukt het niet? Kopieer de diagnose en bewaar of deel die; er staan geen sleutels in.', photo: 'Diagnoseblok met de knop Diagnose kopiëren' },
    ],
    problems: [
      ['“Reageert niet”', 'Staat de unit aan en zit hij op hetzelfde netwerk? Gastnetwerken en wifi-isolatie blokkeren dit.'],
      ['“Niet ondersteund”', 'Deze module spreekt het lokale protocol niet. Bedien de unit met de afstandsbediening of de Gree+-app.'],
    ],
  },
  {
    provider: 'zigbee',
    intro: 'Eigen Zigbee-sensoren (bijvoorbeeld Aqara) meten de echte kamertemperatuur. Ze lopen via de USB-dongle en Zigbee2MQTT op de NAS; geen app of cloud nodig.',
    local: ['Temperatuur, luchtvochtigheid en luchtdruk', 'Batterij en signaalsterkte'],
    vendorFirst: ['Niets: Zigbee-sensoren koppel je rechtstreeks hier.'],
    steps: [
      { text: 'Controleer op Systeem dat de MQTT-verbinding en de USB-dongle groen zijn.' },
      { text: 'Kies Koppelen starten (2 minuten).' },
      { text: 'Houd de knop van de sensor ingedrukt tot het lampje knippert (Aqara: ongeveer 5 seconden).', photo: 'Aqara-sensor met het knopje aan de zijkant' },
      { text: 'Geef de sensor een kamer en wijs hem toe aan de klimaatzone bij Sensoren toewijzen.' },
    ],
    problems: [
      ['Sensor verschijnt niet', 'Houd hem dichter bij de dongle tijdens het koppelen en probeer opnieuw.'],
      ['“Zigbee-dongle offline”', 'Controleer of de USB-dongle in de NAS zit en of de Zigbee2MQTT-container draait.'],
    ],
  },
];

function Matrix({ rows }: { rows: CompatibilityRow[] }) {
  if (!rows.length) return null;
  return (
    <div className="table-wrap">
      <table className="stack-mobile">
        <thead><tr><th scope="col">Model</th><th scope="col">Lokaal</th><th scope="col">In de app van de fabrikant</th><th scope="col">Status</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.model}>
              <td data-label="Model">{r.model}<div className="small muted">{r.protocol}</div></td>
              <td data-label="Lokaal" className="small">{r.local}</td>
              <td data-label="Fabrikant" className="small">{r.vendor_app ?? 'Niets nodig'}</td>
              <td data-label="Status">
                <span className={`pill ${r.status === 'tested' ? 'ok' : r.status === 'unsupported' ? 'error' : 'neutral'}`}>{t(COMPATIBILITY_LABEL, r.status)}</span>
                {r.devices.length > 0 && <div className="small muted">In dit huis: {r.devices.map((d) => d.name).join(', ')}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** /help: what works locally per manufacturer, what needs the manufacturer's app first, steps. */
export function HelpPage() {
  const can = useCan();
  const matrix = useQuery({ queryKey: qk.compatibility, queryFn: api.compatibility, staleTime: 10 * 60_000 });
  const rows = matrix.data && !Array.isArray(matrix.data) ? matrix.data.rows : [];
  return (
    <>
      <header className="page-head">
        <div><span className="eyebrow">Hulp</span><h1 tabIndex={-1}>Help</h1></div>
        {can.admin && <Link className="btn small" to="/installatie">Installatiewizard</Link>}
      </header>
      <nav aria-label="Merken" className="row">
        {PROVIDER_HELP.map((h) => <a key={h.provider} className="btn small" href={`#help-${h.provider}`}>{t(PROVIDER_LABEL, h.provider)}</a>)}
        <a className="btn small" href="#help-verbinding">Verbinding</a>
      </nav>
      {matrix.error && <ErrorNote error={matrix.error} />}
      {PROVIDER_HELP.map((h) => (
        <section key={h.provider} className="card" id={`help-${h.provider}`} aria-labelledby={`help-${h.provider}-h`}>
          <h2 id={`help-${h.provider}-h`}>{t(PROVIDER_LABEL, h.provider)}</h2>
          <p style={{ margin: 0 }}>{h.intro}</p>
          <div className="grid-2 help-cols">
            <div>
              <h3>Wat lokaal werkt</h3>
              <ul className="steps-list">{h.local.map((x) => <li key={x}>{x}</li>)}</ul>
            </div>
            <div>
              <h3>Eerst in de app van de fabrikant</h3>
              <ul className="steps-list">{h.vendorFirst.map((x) => <li key={x}>{x}</li>)}</ul>
            </div>
          </div>
          <h3>Stap voor stap</h3>
          <ol className="steps-list help-steps">
            {h.steps.map((s) => (
              <li key={s.text}>
                {s.text}
                {s.photo && <figure className="photo-placeholder" role="img" aria-label={`Foto volgt: ${s.photo}`}><span aria-hidden="true">Foto volgt: {s.photo}</span></figure>}
              </li>
            ))}
          </ol>
          <details>
            <summary>Instructies van de koppeling</summary>
            <ProviderInstructions provider={h.provider} />
          </details>
          <h3>Als het niet lukt</h3>
          <dl className="facts single">
            {h.problems.map(([q, a]) => <div key={q}><dt>{q}</dt><dd>{a}</dd></div>)}
          </dl>
          <h3>Ondersteunde modellen</h3>
          {matrix.isLoading ? <Loading /> : <Matrix rows={rows.filter((r) => r.provider === h.provider)} />}
        </section>
      ))}
      <section className="card" id="help-verbinding" aria-labelledby="help-verbinding-h">
        <h2 id="help-verbinding-h">Verbinding en inloggen</h2>
        <dl className="facts single">
          <div><dt>“Geen verbinding met de server”</dt><dd>De app ziet de server even niet. Je ziet de laatst bekende gegevens, gedimd; bedienen kan pas weer als de verbinding terug is.</dd></div>
          <div><dt>“De app start op of wordt bijgewerkt”</dt><dd>Na een update of herstart duurt dat meestal minder dan een minuut. Blijft het langer, bekijk dan de logboeken van de API-container op de NAS.</dd></div>
          <div><dt>Opnieuw inloggen bij Cloudflare</dt><dd>Buitenshuis verloopt de Cloudflare Access-sessie na een tijd (standaard 24 uur). De app laadt dan opnieuw zodat je opnieuw kunt inloggen.</dd></div>
          <div><dt>Telefoon “niet meer gekoppeld”</dt><dd>De toegang is ingetrokken of verlopen. Maak een nieuwe QR-code onder Account › Telefoons.</dd></div>
        </dl>
      </section>
    </>
  );
}
