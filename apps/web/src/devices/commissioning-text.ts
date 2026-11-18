// Tekstcontract S-P3-710, afkomstig uit het goedgekeurde Peil-ontwerp.
export const COMMISSIONING_TEXT = {
  "taskTitle": {
    "nl": "Bevestig de bestaande apparaten",
    "en": "Confirm existing devices"
  },
  "taskBody": {
    "nl": "De beveiliging voor ongeteste apparaten staat nog uit. Bevestig zelf welke apparaten werken, of voer eerst de leestest uit. Schakel daarna de beveiliging in voor alle woningen.",
    "en": "Protection for untested devices is still off. Confirm which devices work, or run the read-only test first. Then enable protection for all homes."
  },
  "review": {
    "nl": "Apparaten beoordelen",
    "en": "Review devices"
  },
  "protectionTitle": {
    "nl": "Beveiliging voor ongeteste apparaten",
    "en": "Protection for untested devices"
  },
  "listTitle": {
    "nl": "Teststatus van apparaten",
    "en": "Device test status"
  },
  "name": {
    "nl": "Apparaat en ruimte",
    "en": "Device and room"
  },
  "status": {
    "nl": "Teststatus",
    "en": "Test status"
  },
  "actions": {
    "nl": "Acties",
    "en": "Actions"
  },
  "untested": {
    "nl": "Nog niet getest",
    "en": "Not tested yet"
  },
  "tested": {
    "nl": "Getest in dit huis",
    "en": "Tested in this home"
  },
  "unsupported": {
    "nl": "Niet ondersteund",
    "en": "Not supported"
  },
  "counts": {
    "nl": "Nog niet getest: {count}. Niet ondersteund: {unsupportedCount}.",
    "en": "Not tested yet: {count}. Not supported: {unsupportedCount}."
  },
  "lastConfirmed": {
    "nl": "Laatste bevestigde bediening",
    "en": "Last confirmed operation"
  },
  "confirmedDetail": {
    "nl": "{action} · {time}",
    "en": "{action} · {time}"
  },
  "noneConfirmed": {
    "nl": "Geen bevestigde bediening bekend",
    "en": "No confirmed operation recorded"
  },
  "historyHint": {
    "nl": "Een eerdere bevestiging is een hulpmiddel. Controleer zelf of het apparaat werkt.",
    "en": "A previous confirmation is a guide. Check that the device works yourself."
  },
  "source": {
    "nl": "Bron: opdrachthistorie · opgehaald {time}",
    "en": "Source: command history · retrieved {time}"
  },
  "select": {
    "nl": "Selecteer {name}",
    "en": "Select {name}"
  },
  "selection": {
    "nl": "Geselecteerde apparaten: {count}",
    "en": "Selected devices: {count}"
  },
  "noSelection": {
    "nl": "Selecteer eerst de apparaten die je zelf hebt gecontroleerd.",
    "en": "First select the devices you have checked yourself."
  },
  "readTest": {
    "nl": "Leestest uitvoeren",
    "en": "Run read-only test"
  },
  "readOnly": {
    "nl": "Leest alleen uit; schakelt niets.",
    "en": "Reads only; does not switch anything."
  },
  "readBusy": {
    "nl": "Leestest uitvoeren…",
    "en": "Running read-only test…"
  },
  "readOk": {
    "nl": "Leestest uitgevoerd; bevestig zelf of het apparaat werkt.",
    "en": "Read-only test completed; confirm whether the device works."
  },
  "mark": {
    "nl": "Getest bevestigen",
    "en": "Confirm tested"
  },
  "markNamed": {
    "nl": "Getest bevestigen: {name}",
    "en": "Confirm tested: {name}"
  },
  "markTitle": {
    "nl": "{name} als getest markeren?",
    "en": "Mark {name} as tested?"
  },
  "markBody": {
    "nl": "Bevestig alleen als je meten en, waar toegestaan, bedienen zelf hebt gecontroleerd. De teststatus wordt vastgelegd; dit schakelt het apparaat niet.",
    "en": "Confirm only if you have checked readings and, where allowed, operation yourself. The test status will be recorded; this does not switch the device."
  },
  "note": {
    "nl": "Opmerking bij de test (optioneel)",
    "en": "Test note (optional)"
  },
  "markSubmit": {
    "nl": "Als getest markeren",
    "en": "Mark as tested"
  },
  "bulkTitle": {
    "nl": "Geselecteerde apparaten als getest markeren?",
    "en": "Mark selected devices as tested?"
  },
  "bulkCheck": {
    "nl": "Ik heb alle geselecteerde apparaten zelf gecontroleerd.",
    "en": "I have checked all selected devices myself."
  },
  "bulkSubmit": {
    "nl": "{count} apparaten als getest markeren",
    "en": "Mark {count} devices as tested"
  },
  "bulkReason": {
    "nl": "Bevestig eerst dat je alle geselecteerde apparaten hebt gecontroleerd.",
    "en": "First confirm that you have checked all selected devices."
  },
  "markBusy": {
    "nl": "Teststatus vastleggen…",
    "en": "Recording test status…"
  },
  "marked": {
    "nl": "Teststatus vastgelegd voor {count} apparaten.",
    "en": "Test status recorded for {count} devices."
  },
  "enable": {
    "nl": "Beveiliging inschakelen",
    "en": "Enable protection"
  },
  "enableTitle": {
    "nl": "Beveiliging voor ongeteste apparaten inschakelen?",
    "en": "Enable protection for untested devices?"
  },
  "enableBody": {
    "nl": "Dit geldt voor alle woningen. Daarna worden ongeteste apparaten niet meer bediend, ook niet door schema’s, knoppen en de regelaar. Dit geldt ook voor wachtende opdrachten. De beveiliging blijft aan. Beschermde apparaten blijven altijd alleen meten.",
    "en": "This applies to all homes. Untested devices will no longer be operated, including by schedules, buttons and the controller. This also applies to queued commands. Protection stays on. Protected devices always remain read-only."
  },
  "remaining": {
    "nl": "Bediening wordt voor {count} apparaten geblokkeerd. Ongeteste onderdelen kunnen ook groepen of scènes blokkeren.",
    "en": "Operation will be blocked for {count} devices. Untested members can also block groups or scenes."
  },
  "enableCheck": {
    "nl": "Ik begrijp dat ongeteste apparaten daarna niet meer bediend worden.",
    "en": "I understand that untested devices can no longer be operated afterwards."
  },
  "enableReason": {
    "nl": "Bevestig eerst dat je het gevolg begrijpt.",
    "en": "First confirm that you understand the effect."
  },
  "enableBusy": {
    "nl": "Beveiliging inschakelen…",
    "en": "Enabling protection…"
  },
  "enabled": {
    "nl": "Beveiliging voor ongeteste apparaten staat aan sinds {time}.",
    "en": "Protection for untested devices has been on since {time}."
  },
  "cancel": {
    "nl": "Annuleren",
    "en": "Cancel"
  },
  "close": {
    "nl": "Sluiten",
    "en": "Close"
  },
  "submitted": {
    "nl": "Het verzoek is verstuurd. Je kunt dit venster sluiten terwijl de server de wijziging verwerkt.",
    "en": "The request was sent. You can close this window while the server processes the change."
  },
  "denied": {
    "nl": "Dit apparaat is nog niet getest. Laat een beheerder het eerst onder Apparaten testen en als getest bevestigen in de web-app. Er is geen bedienopdracht verstuurd.",
    "en": "This device has not been tested yet. Ask an administrator to test it and confirm it as tested under Devices in the web app first. No control command was sent."
  },
  "devices": {
    "nl": "Naar Apparaten",
    "en": "Go to Devices"
  },
  "groupDenied": {
    "nl": "De lichtgroep, de gekozen scène of een van de bijbehorende apparaten is nog niet getest. Laat een beheerder de teststatus onder Apparaten controleren en bevestigen. Er is geen bedienopdracht verstuurd.",
    "en": "The light group, the selected scene or one of its devices has not yet been tested. Ask an administrator to check and confirm its test status under Devices. No control command was sent."
  },
  "askAdmin": {
    "nl": "Vraag een beheerder om dit apparaat te testen en als getest te bevestigen.",
    "en": "Ask an administrator to test this device and confirm it as tested."
  },
  "oem": {
    "nl": "OEM regelt zelf · dit apparaat is nog niet getest.",
    "en": "OEM controls the device · this device has not been tested yet."
  },
  "protected": {
    "nl": "Alleen meten — schakelen blijft altijd geblokkeerd, ook na een testbevestiging.",
    "en": "Read-only — switching always remains blocked, even after confirmation as tested."
  },
  "unsupportedHelp": {
    "nl": "Los eerst de protocolfout op en lees het apparaat opnieuw uit.",
    "en": "Resolve the protocol error first, then read the device again."
  },
  "history": {
    "nl": "Testgeschiedenis van {name}",
    "en": "Test history for {name}"
  },
  "historyEntry": {
    "nl": "{time} · {actor} · als getest gemarkeerd",
    "en": "{time} · {actor} · marked as tested"
  },
  "model": {
    "nl": "Model",
    "en": "Model"
  },
  "firmware": {
    "nl": "Firmware",
    "en": "Firmware"
  },
  "notRecorded": {
    "nl": "Niet vastgelegd",
    "en": "Not recorded"
  },
  "noHistory": {
    "nl": "Nog geen testbevestigingen vastgelegd voor dit apparaat.",
    "en": "No test confirmations recorded for this device yet."
  },
  "more": {
    "nl": "Meer laden",
    "en": "Load more"
  },
  "empty": {
    "nl": "Nog geen apparaten gekoppeld. Voeg eerst een apparaat toe.",
    "en": "No devices linked yet. Add a device first."
  },
  "add": {
    "nl": "Apparaat toevoegen",
    "en": "Add device"
  },
  "loading": {
    "nl": "Teststatus ophalen…",
    "en": "Loading test status…"
  },
  "longWait": {
    "nl": "Teststatus ophalen… dit duurt langer dan verwacht.",
    "en": "Loading test status… this is taking longer than expected."
  },
  "refreshing": {
    "nl": "Bijwerken…",
    "en": "Updating…"
  },
  "fetchError": {
    "nl": "Teststatus ophalen is mislukt. Probeer het opnieuw.",
    "en": "Loading test status failed. Try again."
  },
  "mutationError": {
    "nl": "De wijziging kon niet worden bevestigd. Haal de toestand opnieuw op voordat je verdergaat.",
    "en": "The change could not be confirmed. Reload the state before continuing."
  },
  "retry": {
    "nl": "Opnieuw proberen",
    "en": "Try again"
  },
  "reloadState": {
    "nl": "Toestand opnieuw ophalen",
    "en": "Reload state"
  },
  "unknown": {
    "nl": "Onbekend — de teststatus kon niet worden opgehaald.",
    "en": "Unknown — the test status could not be retrieved."
  },
  "stale": {
    "nl": "Verouderd · laatst bekende stand van {time}.",
    "en": "Stale · last known state from {time}."
  },
  "offline": {
    "nl": "Wijzigen kan nu niet: je bent offline. Haal na verbinding eerst de actuele toestand op.",
    "en": "Changes are unavailable while offline. Retrieve the current state after reconnecting."
  },
  "configUnknown": {
    "nl": "De beveiligingsinstelling is onbekend. Ongeteste bediening blijft geblokkeerd. Laat een beheerder de instelling controleren.",
    "en": "The protection setting is unknown. Untested operation remains blocked. Ask an administrator to check the setting."
  },
  "viewer": {
    "nl": "Je kijkt als kijker: je kunt hier niets wijzigen.",
    "en": "You are viewing as a viewer: you cannot change anything here."
  },
  "forbidden": {
    "nl": "Alleen een beheerder mag dit doen.",
    "en": "Only an administrator may do this."
  },
  "child": {
    "nl": "Dit is voor een volwassene.",
    "en": "This is for an adult."
  },
  "childDetails": {
    "nl": "Details voor een volwassene",
    "en": "Details for an adult"
  },
  "meters": {
    "nl": "Meters uitlezen",
    "en": "Read meters"
  },
  "hueRead": {
    "nl": "Hue uitlezen",
    "en": "Read Hue"
  },
  "hueTest": {
    "nl": "Hue testen en bevestigen",
    "en": "Test and confirm Hue"
  },
  "gree": {
    "nl": "Gree controleren",
    "en": "Check Gree"
  },
  "d12Wait": {
    "nl": "Gree-bediening wacht nog op een echte controle van koppelen en teruglezen voor jouw aircomodel en firmware. Tot die controle is vastgelegd, blijft de inbedrijfstelling van Gree in deze wizard geblokkeerd. Gebruik de eigen afstandsbediening voor de controle. Je kunt de rest van de installatie wel afmaken.",
    "en": "Gree operation is waiting for a real pairing and readback check for your air conditioner model and firmware. Gree commissioning in this wizard remains blocked until the check is recorded. Use its own remote control for the check. You can complete the rest of the installation."
  },
  "greeLater": {
    "nl": "Gree later instellen",
    "en": "Set up Gree later"
  },
  "waiting": {
    "nl": "Wacht op controle",
    "en": "Waiting for a check"
  },
  "wizardStep": {
    "nl": "Stap {step} van {total}: {title}",
    "en": "Step {step} of {total}: {title}"
  },
  "wizardNav": {
    "nl": "Stappen van de installatie",
    "en": "Installation steps"
  },
  "rollback": {
    "nl": "Terugval voorbereiden",
    "en": "Prepare for rollback"
  },
  "rollbackHelp": {
    "nl": "Ga je terug naar een oudere appversie? Kies welke wizard je straks opent in deze browser. Die hervat de eigen voortgang. Bij een eerste gebruik worden de huidige stap en de doorlopen leesstappen overgenomen.",
    "en": "Going back to an older app version? Choose the wizard you will open in this browser. It resumes its own progress. On first use it starts with your current step and completed reading steps."
  },
  "wizardOneHue": {
    "nl": "Wizard met één Hue-stap",
    "en": "Wizard with one Hue step"
  },
  "wizardTwoHue": {
    "nl": "Wizard met twee Hue-stappen",
    "en": "Wizard with two Hue steps"
  },
  "rollbackReady": {
    "nl": "De voortgang staat klaar voor ‘{wizard}’.",
    "en": "Progress is ready for ‘{wizard}’."
  },
  "rollbackFailed": {
    "nl": "De terugval kon niet worden voorbereid. Controleer of deze browser de wizardvoortgang kan lezen en opslaan.",
    "en": "Rollback could not be prepared. Check that this browser can read and save wizard progress."
  },
  "previous": {
    "nl": "Vorige",
    "en": "Previous"
  },
  "next": {
    "nl": "Volgende: {title}",
    "en": "Next: {title}"
  },
  "baseConfigured": {
    "nl": "De basis is ingericht. Wachtende apparaatcontroles blijven zichtbaar bij Apparaten.",
    "en": "Basic setup is configured. Pending device checks remain visible under Devices."
  },
  "replacementBlocked": {
    "nl": "Vervangen kan niet: een knopkoppeling verwijst nog naar dit beschermde apparaat. Verwijder eerst die koppeling.",
    "en": "Replacement is blocked: a button mapping still refers to this protected device. Remove that mapping first."
  }
} as const;
export function commissioningText(key: keyof typeof COMMISSIONING_TEXT, values: Record<string, string | number> = {}, locale: "nl" | "en" = "nl"): string {
  return Object.entries(values).reduce((s, [name, value]) => s.replaceAll(`{${name}}`, String(value)), COMMISSIONING_TEXT[key][locale] as string);
}
