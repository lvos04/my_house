package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import nl.huis.smarthome.BuildConfig
import nl.huis.smarthome.R
import nl.huis.smarthome.core.Alert
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.RealtimeState
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.NotifAction
import nl.huis.smarthome.ui.NotifUi
import nl.huis.smarthome.ui.MeldingSoort
import nl.huis.smarthome.ui.PeilUi
import nl.huis.smarthome.ui.components.ErnstLabel
import nl.huis.smarthome.ui.components.KnopSoort
import nl.huis.smarthome.ui.components.Note
import nl.huis.smarthome.ui.components.PeilIcoon
import nl.huis.smarthome.ui.components.PeilKnop
import nl.huis.smarthome.ui.components.ConnectionBanner
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.InfoBox
import nl.huis.smarthome.ui.components.SectionTitle
import nl.huis.smarthome.ui.components.relativeText
import nl.huis.smarthome.ui.theme.Huis
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

@Composable
fun MoreScreen(state: HomeState, vm: MainViewModel, notif: NotifUi) {
    var confirmUnpair by rememberSaveable { mutableStateOf(false) }
    val unpairing by vm.unpairing.collectAsStateWithLifecycle()
    val alerts = state.overview?.alerts.orEmpty().filterNot { it.acknowledged }

    Column(
        Modifier
            .fillMaxSize()
            // The keyboard must not cover the address and Cloudflare fields.
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            stringResource(R.string.nav_more),
            style = MaterialTheme.typography.headlineSmall,
            modifier = Modifier.semantics { heading() },
        )
        ConnectionBanner(state, vm::retry)

        // Meldingen van het huis
        SectionTitle(stringResource(R.string.alerts))
        HuisCard {
            if (alerts.isEmpty()) {
                Text(stringResource(R.string.no_alerts), color = Huis.colors.muted)
            }
            alerts.forEachIndexed { i, a ->
                AlertRow(a, canAck = state.controlsEnabled) { vm.acknowledge(a) }
                if (i < alerts.lastIndex) HorizontalDivider(color = Huis.peil.border)
            }
        }

        // Meldingen op deze telefoon
        SectionTitle(stringResource(R.string.notifications_title))
        HuisCard {
            // On/off is a setting, not health: bell or bell-off in ink, never a green or amber dot.
            Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                PeilIcoon(
                    if (notif.canPost) R.drawable.lucide_bell else R.drawable.lucide_bell_off,
                    Huis.colors.ink,
                    Modifier.padding(top = 2.dp),
                )
                Text(
                    stringResource(if (notif.canPost) R.string.notifications_on else R.string.notifications_off),
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.weight(1f),
                )
            }
            if (!notif.canPost && notif.waiting > 0) {
                Note(
                    LocalContext.current.resources.getQuantityString(R.plurals.notifications_waiting_count, notif.waiting, notif.waiting),
                    MeldingSoort.BEPERKT,
                )
            }
            val requesting = !notif.canPost && notif.action == NotifAction.REQUEST
            PeilKnop(
                stringResource(if (requesting) R.string.notifications_allow else R.string.notifications_settings),
                onClick = if (requesting) notif.onAllow else notif.onSettings,
                soort = KnopSoort.SECUNDAIR,
                modifier = Modifier.fillMaxWidth(),
            )
        }

        // Verbinding
        SectionTitle(stringResource(R.string.connection))
        var editing by rememberSaveable { mutableStateOf(false) }
        var savedNote by rememberSaveable { mutableStateOf(false) }
        HuisCard {
            KeyValue(stringResource(R.string.home), state.pairing?.homeName ?: "–")
            KeyValue(stringResource(R.string.phone_role), roleLabel(state))
            KeyValue(stringResource(R.string.lan_address), state.pairing?.lanUrl ?: stringResource(R.string.not_set), stacked = true)
            KeyValue(stringResource(R.string.remote_address), state.pairing?.remoteUrl ?: stringResource(R.string.not_set), stacked = true)
            KeyValue(
                stringResource(R.string.current_route),
                when {
                    !state.online || state.route == null -> stringResource(R.string.route_none)
                    state.route?.kind == RouteKind.LAN -> stringResource(R.string.route_lan)
                    else -> stringResource(R.string.route_remote)
                },
            )
            KeyValue(
                stringResource(R.string.realtime),
                if (state.realtime == RealtimeState.CONNECTED) stringResource(R.string.realtime_on) else stringResource(R.string.realtime_off),
            )
            state.pairing?.tokenExpiresAt?.let { expires ->
                NlFormat.parseInstant(expires)?.let { at ->
                    val date = at.atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("d MMMM yyyy", Locale.forLanguageTag("nl-NL")))
                    KeyValue(stringResource(R.string.key_valid_until), date)
                }
            }
            if (savedNote && !editing) {
                // Saved is done, not healthy: a neutral check.
                SavedLine(stringResource(R.string.addresses_saved))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                PeilKnop(stringResource(R.string.retry), onClick = vm::retry, soort = KnopSoort.SECUNDAIR, modifier = Modifier.weight(1f))
                PeilKnop(
                    stringResource(R.string.addresses_edit),
                    onClick = { editing = !editing; savedNote = false },
                    soort = KnopSoort.SECUNDAIR,
                    modifier = Modifier.weight(1f),
                )
            }
        }
        if (editing) {
            AddressEditor(
                initialLan = state.pairing?.lanUrl.orEmpty(),
                initialRemote = state.pairing?.remoteUrl.orEmpty(),
                onSave = { lan, remote ->
                    val error = vm.updateAddresses(lan, remote)
                    if (error == null) {
                        editing = false
                        savedNote = true
                    }
                    error
                },
                onCancel = { editing = false },
            )
        }

        CloudflareAccessCard(vm)

        PeilKnop(
            stringResource(R.string.unpair),
            onClick = { confirmUnpair = true },
            enabled = !unpairing,
            soort = KnopSoort.GEVAAR,
            modifier = Modifier.fillMaxWidth(),
        )

        Text(
            stringResource(R.string.version, BuildConfig.VERSION_NAME),
            color = Huis.colors.muted,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.align(Alignment.CenterHorizontally),
        )
    }

    if (confirmUnpair || unpairing) {
        AlertDialog(
            onDismissRequest = { if (!unpairing) confirmUnpair = false },
            title = { Text(stringResource(R.string.unpair_title)) },
            text = {
                if (unpairing) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = Huis.peil.primary)
                        Text(stringResource(R.string.unpair_busy))
                    }
                } else {
                    Text(stringResource(R.string.unpair_body))
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmUnpair = false
                        vm.unpair()
                    },
                    enabled = !unpairing,
                    modifier = Modifier.heightIn(min = 48.dp),
                ) { Text(stringResource(R.string.unpair), color = Huis.peil.destructive, fontWeight = FontWeight.SemiBold) }
            },
            dismissButton = {
                TextButton(
                    onClick = { confirmUnpair = false },
                    enabled = !unpairing,
                    modifier = Modifier.heightIn(min = 48.dp),
                ) { Text(stringResource(R.string.cancel), color = Huis.colors.ink) }
            },
        )
    }
}

@Composable
private fun roleLabel(state: HomeState): String = stringResource(
    when {
        state.isAdmin -> R.string.role_admin
        state.canControl -> R.string.role_control
        else -> R.string.role_viewer
    },
)

@Composable
internal fun huisFieldColors() = OutlinedTextFieldDefaults.colors(
    unfocusedBorderColor = Huis.peil.input,
    focusedBorderColor = Huis.peil.ring,
    focusedLabelColor = Huis.peil.foreground,
    unfocusedLabelColor = Huis.peil.mutedForeground,
    cursorColor = Huis.peil.foreground,
    focusedContainerColor = Huis.peil.card,
    unfocusedContainerColor = Huis.peil.card,
)

/**
 * "Adressen bewerken" (verbeterlijst 37): the same rules as a QR code. The token only goes to an
 * address that answers as the paired server, so a typo cannot leak it.
 */
@Composable
private fun AddressEditor(
    initialLan: String,
    initialRemote: String,
    onSave: (String, String) -> String?,
    onCancel: () -> Unit,
) {
    var lan by rememberSaveable { mutableStateOf(initialLan) }
    var remote by rememberSaveable { mutableStateOf(initialRemote) }
    var error by rememberSaveable { mutableStateOf<String?>(null) }
    val focus = LocalFocusManager.current
    val save: () -> Unit = {
        focus.clearFocus()
        error = onSave(lan, remote)
    }
    SectionTitle(stringResource(R.string.addresses_edit))
    HuisCard {
        Text(stringResource(R.string.addresses_help), color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium)
        OutlinedTextField(
            value = lan,
            onValueChange = { lan = it; error = null },
            label = { Text(stringResource(R.string.lan_address)) },
            placeholder = { Text(stringResource(R.string.lan_address_hint)) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
            colors = huisFieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = remote,
            onValueChange = { remote = it; error = null },
            label = { Text(stringResource(R.string.remote_address)) },
            placeholder = { Text(stringResource(R.string.remote_address_hint)) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { save() }),
            colors = huisFieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )
        error?.let {
            InfoBox(it, warn = true, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Assertive })
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PeilKnop(stringResource(R.string.save), onClick = save, soort = KnopSoort.PRIMAIR)
            PeilKnop(stringResource(R.string.cancel), onClick = onCancel, soort = KnopSoort.STIL)
        }
    }
}

@Composable
private fun CloudflareAccessCard(vm: MainViewModel) {
    val initial = remember { vm.cfAccess() }
    var id by rememberSaveable { mutableStateOf(initial.first.orEmpty()) }
    var secret by rememberSaveable { mutableStateOf(initial.second.orEmpty()) }
    var saved by rememberSaveable { mutableStateOf(false) }
    val focus = LocalFocusManager.current
    val save: () -> Unit = {
        focus.clearFocus()
        vm.saveCfAccess(id, secret)
        saved = true
    }

    SectionTitle(stringResource(R.string.cf_access))
    HuisCard {
        Text(stringResource(R.string.cf_access_help), color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium)
        OutlinedTextField(
            value = id,
            onValueChange = { id = it; saved = false },
            label = { Text(stringResource(R.string.cf_client_id)) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii, imeAction = ImeAction.Next),
            colors = huisFieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = secret,
            onValueChange = { secret = it; saved = false },
            label = { Text(stringResource(R.string.cf_client_secret)) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { save() }),
            colors = huisFieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            PeilKnop(stringResource(R.string.save), onClick = save, soort = KnopSoort.PRIMAIR)
            if (saved) SavedLine(stringResource(R.string.saved))
        }
    }
}

/** "Opgeslagen": done is not healthy, so a neutral check in ink (Peil), read aloud when it appears. */
@Composable
private fun SavedLine(text: String) {
    Row(
        Modifier.semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Polite },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        PeilIcoon(R.drawable.lucide_check, Huis.colors.ink, size = 18.dp)
        Text(text, color = Huis.colors.ink, style = MaterialTheme.typography.bodySmall)
    }
}

/** An alert with its severity label (Info · Middel · Hoog) instead of a coloured dot, and "Gezien". */
@Composable
private fun AlertRow(alert: Alert, canAck: Boolean, onAck: () -> Unit) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            ErnstLabel(PeilUi.ernst(alert.severity))
            Spacer(Modifier.weight(1f))
            if (canAck) {
                PeilKnop(stringResource(R.string.acknowledge), onClick = onAck, soort = KnopSoort.SECUNDAIR)
            }
        }
        Text(alert.title, style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold))
        val sub = listOfNotNull(alert.detail, alert.createdAt?.let { relativeText(it) }).joinToString(" · ")
        Text(sub, color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun KeyValue(key: String, value: String, stacked: Boolean = false) {
    if (stacked) {
        Column(Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(key, color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium)
            Text(value, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
        }
        return
    }
    Row(Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(key, color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(12.dp))
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
            textAlign = TextAlign.End,
            modifier = Modifier.weight(1.2f, fill = false),
        )
    }
}
