package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.BuildConfig
import nl.huis.smarthome.R
import nl.huis.smarthome.core.Alert
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.RealtimeState
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.StatusDot
import nl.huis.smarthome.ui.theme.Huis

@Composable
fun MoreScreen(state: HomeState, vm: MainViewModel) {
    var confirmUnpair by remember { mutableStateOf(false) }
    val alerts = state.overview?.alerts.orEmpty().filterNot { it.acknowledged }
    val isAdmin = state.pairing?.role == "admin"

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(stringResource(R.string.nav_more), style = MaterialTheme.typography.headlineMedium)

        // Meldingen
        Text(stringResource(R.string.alerts), style = MaterialTheme.typography.titleMedium)
        HuisCard(radius = 20) {
            if (alerts.isEmpty()) {
                Text(stringResource(R.string.no_alerts), color = Huis.colors.muted)
            }
            alerts.forEachIndexed { i, a ->
                AlertRow(a, canAck = state.controlsEnabled && isAdmin) { vm.acknowledge(a) }
                if (i < alerts.lastIndex) HorizontalDivider(color = Huis.colors.outline.copy(alpha = 0.6f))
            }
        }

        // Verbinding
        Text(stringResource(R.string.connection), style = MaterialTheme.typography.titleMedium)
        HuisCard(radius = 20) {
            KeyValue(stringResource(R.string.home), state.pairing?.homeName ?: "–")
            KeyValue(stringResource(R.string.lan_address), state.pairing?.lanUrl ?: stringResource(R.string.not_set))
            KeyValue(stringResource(R.string.remote_address), state.pairing?.remoteUrl ?: stringResource(R.string.not_set))
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
            OutlinedButton(onClick = vm::retry, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.retry), color = Huis.colors.ink)
            }
        }

        CloudflareAccessCard(vm)

        Button(
            onClick = { confirmUnpair = true },
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = Huis.colors.surface, contentColor = Huis.colors.error),
        ) { Text(stringResource(R.string.unpair)) }

        Text(
            stringResource(R.string.version, BuildConfig.VERSION_NAME),
            color = Huis.colors.muted,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.align(Alignment.CenterHorizontally),
        )
    }

    if (confirmUnpair) {
        AlertDialog(
            onDismissRequest = { confirmUnpair = false },
            title = { Text(stringResource(R.string.unpair_title)) },
            text = { Text(stringResource(R.string.unpair_body)) },
            confirmButton = {
                TextButton(onClick = { confirmUnpair = false; vm.unpair() }) {
                    Text(stringResource(R.string.unpair), color = Huis.colors.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmUnpair = false }) { Text(stringResource(R.string.cancel)) }
            },
        )
    }
}

@Composable
private fun CloudflareAccessCard(vm: MainViewModel) {
    val initial = remember { vm.cfAccess() }
    var id by rememberSaveable { mutableStateOf(initial.first.orEmpty()) }
    var secret by rememberSaveable { mutableStateOf(initial.second.orEmpty()) }
    var saved by remember { mutableStateOf(false) }

    Text(stringResource(R.string.cf_access), style = MaterialTheme.typography.titleMedium)
    HuisCard(radius = 20) {
        Text(stringResource(R.string.cf_access_help), color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium)
        OutlinedTextField(
            value = id,
            onValueChange = { id = it; saved = false },
            label = { Text(stringResource(R.string.cf_client_id)) },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = secret,
            onValueChange = { secret = it; saved = false },
            label = { Text(stringResource(R.string.cf_client_secret)) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = { vm.saveCfAccess(id, secret); saved = true },
                colors = ButtonDefaults.buttonColors(containerColor = Huis.colors.ink, contentColor = Huis.colors.ground),
            ) { Text(stringResource(R.string.save)) }
            if (saved) Text(stringResource(R.string.saved), color = Huis.colors.ok)
        }
    }
}

@Composable
private fun AlertRow(alert: Alert, canAck: Boolean, onAck: () -> Unit) {
    val color = when (alert.severity) {
        "error" -> Huis.colors.error
        "warn" -> Huis.colors.warn
        else -> Huis.colors.cool
    }
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Column(Modifier.padding(top = 7.dp)) { StatusDot(color) }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(alert.title, style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold))
            val sub = listOfNotNull(alert.detail, NlFormat.relative(alert.createdAt)).joinToString(" · ")
            Text(sub, color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
        }
        if (canAck) TextButton(onClick = onAck) { Text(stringResource(R.string.acknowledge)) }
    }
}

@Composable
private fun KeyValue(key: String, value: String) {
    Row(Modifier.fillMaxWidth().heightIn(min = 40.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(key, color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        Text(value, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
    }
}
