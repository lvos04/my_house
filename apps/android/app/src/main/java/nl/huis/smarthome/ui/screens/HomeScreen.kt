package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedIconButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.LightGroup
import nl.huis.smarthome.core.LivePower
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.OfflineBanner
import nl.huis.smarthome.ui.components.Pill
import nl.huis.smarthome.ui.components.QualityLabel
import nl.huis.smarthome.ui.components.RouteChip
import nl.huis.smarthome.ui.theme.Huis
import java.time.LocalTime

@Composable
fun HomeScreen(
    state: HomeState,
    vm: MainViewModel,
    onOpenZone: (String) -> Unit,
    onOpenAlerts: () -> Unit,
) {
    val overview = state.overview
    val dim = if (state.online) 1f else 0.55f
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            overview?.home?.name ?: state.pairing?.homeName ?: "",
                            color = Huis.colors.muted,
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        RouteChip(state)
                    }
                    Text(greeting(), style = MaterialTheme.typography.headlineMedium, color = Huis.colors.ink)
                }
                val openAlerts = overview?.alerts?.count { !it.acknowledged } ?: 0
                val label = stringResource(R.string.alerts_button, openAlerts)
                OutlinedIconButton(
                    onClick = onOpenAlerts,
                    modifier = Modifier.size(48.dp).semantics { contentDescription = label },
                    border = BorderStroke(1.dp, Huis.colors.outline),
                    colors = IconButtonDefaults.outlinedIconButtonColors(containerColor = Huis.colors.surface),
                ) {
                    BadgedBox(badge = { if (openAlerts > 0) Badge(containerColor = Huis.colors.warn) { Text("$openAlerts") } }) {
                        Icon(Icons.Outlined.Notifications, contentDescription = null, tint = Huis.colors.ink)
                    }
                }
            }
        }
        item { OfflineBanner(state, vm::retry) }

        val zones = overview?.zones.orEmpty()
        if (overview != null && zones.isEmpty()) {
            item { Text(stringResource(R.string.no_zones), color = Huis.colors.muted) }
        }
        items(zones, key = { it.id }) { zone ->
            ZoneCard(
                zone = zone,
                draft = state.drafts[zone.id],
                pending = zone.id in state.pending,
                enabled = state.controlsEnabled && zone.online,
                dim = dim,
                onNudge = { vm.nudge(zone, it) },
                onOpen = { onOpenZone(zone.id) },
            )
        }

        val lights = overview?.lights.orEmpty()
        if (lights.isNotEmpty()) {
            item {
                Text(stringResource(R.string.lights), style = MaterialTheme.typography.titleSmall, color = Huis.colors.muted)
            }
            items(lights.chunked(2)) { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    row.forEach { g ->
                        LightTile(g, enabled = state.controlsEnabled, modifier = Modifier.weight(1f).alpha(dim)) { vm.toggle(g) }
                    }
                    if (row.size == 1) Box(Modifier.weight(1f))
                }
            }
        }

        overview?.powerNow?.let { power -> item { PowerCard(power, Modifier.alpha(dim)) } }
    }
}

@Composable
private fun greeting(): String {
    val h = LocalTime.now().hour
    return stringResource(
        when (h) {
            in 6..11 -> R.string.greeting_morning
            in 12..17 -> R.string.greeting_afternoon
            in 18..23 -> R.string.greeting_evening
            else -> R.string.greeting_night
        },
    )
}

@Composable
fun modeColors(mode: String): Pair<androidx.compose.ui.graphics.Color, androidx.compose.ui.graphics.Color> = when (mode) {
    "heat" -> Huis.colors.heatContainer to Huis.colors.heat
    "cool" -> Huis.colors.coolContainer to Huis.colors.cool
    else -> Huis.colors.surfaceMuted to Huis.colors.muted
}

@Composable
private fun ZoneCard(
    zone: Zone,
    draft: Double?,
    pending: Boolean,
    enabled: Boolean,
    dim: Float,
    onNudge: (Double) -> Unit,
    onOpen: () -> Unit,
) {
    val stale = zone.temperature.stale || !zone.online
    HuisCard(Modifier.clickable(onClick = onOpen)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(zone.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            val (bg, fg) = modeColors(zone.mode)
            if (zone.override != null) {
                Pill(stringResource(R.string.override_until, NlFormat.clock(zone.override?.until)), Huis.colors.warnContainer, Huis.colors.onWarnContainer)
            } else {
                Pill(NlFormat.mode(zone.mode), bg, fg)
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f).alpha(dim)) {
                Text(
                    NlFormat.temperature(zone.temperature.value),
                    style = MaterialTheme.typography.displayMedium,
                    color = if (stale) Huis.colors.dimmed else Huis.colors.ink,
                )
                when {
                    !zone.online -> Text(stringResource(R.string.unit_offline), color = Huis.colors.warn, style = MaterialTheme.typography.bodySmall)
                    zone.temperature.stale -> Text(
                        zone.notice ?: stringResource(R.string.stale_sensor),
                        color = Huis.colors.warn,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    else -> QualityLabel(zone.temperature.quality, NlFormat.relative(zone.temperature.at))
                }
            }
            if (zone.mode != "off") {
                SetpointControl(
                    value = draft ?: zone.targetC,
                    enabled = enabled,
                    onNudge = onNudge,
                    step = zone.capabilities.stepC,
                )
            }
        }
        if (pending || draft != null) {
            Text(stringResource(R.string.sent_pending), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
fun SetpointControl(value: Double?, enabled: Boolean, onNudge: (Double) -> Unit, step: Double, big: Boolean = false) {
    val btn = if (big) 64.dp else 48.dp
    val lower = stringResource(R.string.target_lower)
    val higher = stringResource(R.string.target_higher)
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        FilledTonalIconButton(
            onClick = { onNudge(-step) },
            enabled = enabled,
            modifier = Modifier.size(btn).semantics { contentDescription = lower },
            colors = IconButtonDefaults.filledTonalIconButtonColors(containerColor = Huis.colors.surfaceMuted, contentColor = Huis.colors.ink),
        ) { Text("−", style = MaterialTheme.typography.headlineSmall) }
        if (!big) {
            Column(Modifier.width(60.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text(stringResource(R.string.target), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
                Text(NlFormat.temperature(value), style = MaterialTheme.typography.titleMedium)
            }
        }
        FilledTonalIconButton(
            onClick = { onNudge(step) },
            enabled = enabled,
            modifier = Modifier.size(btn).semantics { contentDescription = higher },
            colors = IconButtonDefaults.filledTonalIconButtonColors(containerColor = Huis.colors.surfaceMuted, contentColor = Huis.colors.ink),
        ) { Text("+", style = MaterialTheme.typography.headlineSmall) }
    }
}

@Composable
private fun LightTile(group: LightGroup, enabled: Boolean, modifier: Modifier = Modifier, onToggle: () -> Unit) {
    val on = group.on && group.reachable
    Surface(
        onClick = onToggle,
        enabled = enabled && group.reachable,
        modifier = modifier.heightIn(min = 76.dp),
        shape = RoundedCornerShape(20.dp),
        color = if (on) Huis.colors.light else Huis.colors.surface,
        border = if (on) null else BorderStroke(1.dp, Huis.colors.outline),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(group.name, style = MaterialTheme.typography.titleSmall, color = Huis.colors.ink)
            val sub = when {
                !group.reachable -> stringResource(R.string.lights_unreachable)
                !group.on -> stringResource(R.string.lights_off)
                else -> listOfNotNull(group.scene, group.brightness?.let { "$it%" }).joinToString(" · ").ifBlank { "Aan" }
            }
            Text(sub, style = MaterialTheme.typography.bodySmall, color = if (on) Huis.colors.onLight else Huis.colors.muted)
        }
    }
}

@Composable
private fun PowerCard(power: LivePower, modifier: Modifier = Modifier) {
    HuisCard(modifier) {
        Row {
            Text(stringResource(R.string.power_now), style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            Text("P1 · " + NlFormat.relative(power.at), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
        }
        Text(NlFormat.watts(power.totalW), style = MaterialTheme.typography.displaySmall)
        val parts = power.parts.filter { (it.watts ?: 0.0) > 0 }
        val total = parts.sumOf { it.watts ?: 0.0 }.takeIf { it > 0 }
        if (total != null) {
            Row(
                Modifier.fillMaxWidth().height(8.dp).clip(RoundedCornerShape(4.dp)),
                horizontalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                parts.forEach { p ->
                    Box(Modifier.weight(((p.watts ?: 0.0) / total).toFloat().coerceAtLeast(0.01f)).height(8.dp).background(roleColor(p.role)))
                }
            }
        }
        Text(
            power.parts.joinToString(" · ") { "${it.label} ${NlFormat.watts(it.watts)}" },
            color = Huis.colors.muted,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
fun roleColor(role: String): androidx.compose.ui.graphics.Color = when (role) {
    "airco" -> Huis.colors.cool
    "boiler" -> Huis.colors.boiler
    else -> Huis.colors.rest
}
