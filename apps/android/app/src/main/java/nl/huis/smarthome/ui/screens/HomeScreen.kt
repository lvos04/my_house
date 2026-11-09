package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.LightGroup
import nl.huis.smarthome.core.LivePower
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.Quality
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.BannerKind
import nl.huis.smarthome.ui.CommandLine
import nl.huis.smarthome.ui.CommandUi
import nl.huis.smarthome.ui.ConnectionUi
import nl.huis.smarthome.ui.LightsUi
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.NotifPrompt
import nl.huis.smarthome.ui.NotifUi
import nl.huis.smarthome.ui.UnitState
import nl.huis.smarthome.ui.ZoneUi
import nl.huis.smarthome.ui.components.CommandStatusText
import nl.huis.smarthome.ui.components.ConnectionBanner
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.NotificationPromptCard
import nl.huis.smarthome.ui.components.QualityLabel
import nl.huis.smarthome.ui.components.ReadOnlyNote
import nl.huis.smarthome.ui.components.RouteChip
import nl.huis.smarthome.ui.components.SetpointControl
import nl.huis.smarthome.ui.components.TargetReadout
import nl.huis.smarthome.ui.components.UnitNotLinked
import nl.huis.smarthome.ui.components.ZonePillView
import nl.huis.smarthome.ui.components.qualityText
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.noFontScale
import java.time.LocalTime

@Composable
fun HomeScreen(
    state: HomeState,
    vm: MainViewModel,
    onOpenZone: (String) -> Unit,
    onOpenAlerts: () -> Unit,
    notif: NotifUi? = null,
) {
    val overview = state.overview
    val dim = if (state.online) 1f else 0.55f
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item(key = "header") {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            overview?.home?.name ?: state.pairing?.homeName ?: "",
                            color = Huis.colors.muted,
                            style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.weight(1f, fill = false),
                        )
                        RouteChip(state)
                    }
                    Text(
                        greeting(),
                        style = MaterialTheme.typography.headlineMedium,
                        color = Huis.colors.ink,
                        modifier = Modifier.semantics { heading() },
                    )
                }
                val openAlerts = overview?.alerts?.count { !it.acknowledged } ?: 0
                val label = stringResource(R.string.alerts_button, openAlerts)
                OutlinedIconButton(
                    onClick = onOpenAlerts,
                    modifier = Modifier.size(48.dp).semantics { contentDescription = label },
                    border = BorderStroke(1.dp, Huis.colors.controlOutline),
                    colors = IconButtonDefaults.outlinedIconButtonColors(containerColor = Huis.colors.surface),
                ) {
                    BadgedBox(badge = { if (openAlerts > 0) Badge(containerColor = Huis.colors.warn, contentColor = Huis.colors.surface) { Text("$openAlerts") } }) {
                        Icon(Icons.Outlined.Notifications, contentDescription = null, tint = Huis.colors.ink)
                    }
                }
            }
        }
        if (ConnectionUi.banner(state).kind != BannerKind.NONE) {
            item(key = "banner") { ConnectionBanner(state, vm::retry) }
        }
        if (state.readOnly) item(key = "readonly") { ReadOnlyNote(state) }
        if (notif != null && notif.prompt != NotifPrompt.NONE) {
            item(key = "notif") {
                NotificationPromptCard(notif.prompt, notif.waiting, notif.onAllow, notif.onLater, notif.onSettings)
            }
        }

        val zones = overview?.zones.orEmpty()
        if (overview != null && zones.isEmpty()) {
            item(key = "nozones") { Text(stringResource(R.string.no_zones), color = Huis.colors.muted) }
        }
        items(zones, key = { it.id }) { zone ->
            ZoneCard(
                zone = zone,
                state = state,
                dim = dim,
                onNudge = { vm.nudge(zone, it) },
                onOpen = { onOpenZone(zone.id) },
            )
        }

        val lights = LightsUi.visible(overview?.lights.orEmpty())
        if (lights.isNotEmpty()) {
            item(key = "lights-title") {
                Text(
                    stringResource(R.string.lights),
                    style = MaterialTheme.typography.titleSmall,
                    color = Huis.colors.muted,
                    modifier = Modifier.semantics { heading() },
                )
            }
            items(lights.chunked(2), key = { row -> row.joinToString("|") { it.id } }) { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    row.forEach { g ->
                        LightTile(
                            group = g,
                            line = CommandUi.line(state, g),
                            enabled = state.controlsEnabled,
                            readOnly = state.readOnly,
                            modifier = Modifier.weight(1f).alpha(dim),
                        ) { vm.toggle(g) }
                    }
                    if (row.size == 1) Box(Modifier.weight(1f))
                }
            }
        }

        overview?.powerNow?.let { power -> item(key = "power") { PowerCard(power, Modifier.alpha(dim)) } }
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
private fun ZoneCard(
    zone: Zone,
    state: HomeState,
    dim: Float,
    onNudge: (Double) -> Unit,
    onOpen: () -> Unit,
) {
    val unit = ZoneUi.unitState(zone)
    val controls = ZoneUi.controls(zone, state)
    val openLabel = stringResource(R.string.zone_open, zone.name)
    HuisCard(onClick = onOpen, onClickLabel = openLabel) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(zone.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            ZonePillView(zone)
        }
        if (unit == UnitState.NOT_LINKED) {
            if (ZoneUi.hasSensor(zone)) Reading(zone, Modifier.alpha(dim))
            UnitNotLinked(zone, canManage = state.isAdmin)
            return@HuisCard
        }
        val shown = state.shownTarget(zone)
        val target: @Composable () -> Unit = {
            when {
                controls.visible && unit == UnitState.ON ->
                    SetpointControl(value = shown, enabled = controls.setpointEnabled, onNudge = onNudge, step = zone.capabilities.stepC)
                unit == UnitState.ON -> TargetReadout(shown, Modifier.alpha(dim))
                else -> Unit
            }
        }
        if (LocalDensity.current.fontScale > 1.3f) {
            // Large font: reading and +/− below each other, so neither gets squeezed.
            Reading(zone, Modifier.alpha(dim))
            target()
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Reading(zone, Modifier.weight(1f).alpha(dim))
                target()
            }
        }
        when (unit) {
            UnitState.OFF -> Text(stringResource(R.string.zone_off_short), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
            UnitState.OFFLINE -> Text(stringResource(R.string.unit_unreachable), color = Huis.colors.warn, style = MaterialTheme.typography.bodySmall)
            else -> Unit
        }
        zone.notice?.takeIf { zone.noticeIsWarning && unit == UnitState.ON }?.let {
            Text(it, color = Huis.colors.warn, style = MaterialTheme.typography.bodySmall)
        }
        CommandStatusText(CommandUi.line(state, zone))
    }
}

/** Room temperature with its quality label (or "Sensor verouderd"). */
@Composable
private fun Reading(zone: Zone, modifier: Modifier = Modifier) {
    val t = zone.temperature
    Column(modifier) {
        Text(
            NlFormat.temperature(t.value),
            // Already 48 sp: kept at that size so it never breaks over two lines next to +/−.
            style = MaterialTheme.typography.displayMedium.noFontScale(),
            color = if (t.stale || t.value == null) Huis.colors.dimmed else Huis.colors.ink,
        )
        if (t.stale) {
            Text(
                stringResource(R.string.stale_sensor_since, NlFormat.relative(t.at)),
                color = Huis.colors.warn,
                style = MaterialTheme.typography.bodySmall,
            )
        } else {
            QualityLabel(t.quality, t.at?.let { NlFormat.relative(it) })
        }
    }
}

/**
 * A light group: one tap switches it. TalkBack hears a switch with its state ("aan"/"uit");
 * a viewer phone sees the state without a switch.
 */
@Composable
private fun LightTile(
    group: LightGroup,
    line: CommandLine?,
    enabled: Boolean,
    readOnly: Boolean,
    modifier: Modifier = Modifier,
    onToggle: () -> Unit,
) {
    val on = group.on && group.reachable
    val shape = RoundedCornerShape(20.dp)
    val stateText = stringResource(
        when {
            !group.reachable -> R.string.lights_unreachable
            group.on -> R.string.light_on
            else -> R.string.lights_off
        },
    )
    val interaction = if (readOnly) {
        Modifier.semantics { stateDescription = stateText }
    } else {
        Modifier.toggleable(
            value = group.on,
            enabled = enabled && group.reachable,
            role = Role.Switch,
            onValueChange = { onToggle() },
        )
    }
    Surface(
        modifier = modifier
            .heightIn(min = 76.dp)
            .clip(shape)
            .then(interaction),
        shape = shape,
        color = if (on) Huis.colors.light else Huis.colors.surface,
        border = if (on) null else BorderStroke(1.dp, Huis.colors.controlOutline),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(group.name, style = MaterialTheme.typography.titleSmall, color = if (on) Huis.colors.onLight else Huis.colors.ink)
            val sub = when {
                !group.reachable -> stateText
                !group.on -> stateText
                else -> listOfNotNull(
                    group.scene,
                    group.brightness?.let { "$it%" },
                    group.lightsTotal.takeIf { it > 1 && group.lightsOn in 1 until it }?.let { stringResource(R.string.lights_partly, group.lightsOn, it) },
                ).joinToString(" · ").ifBlank { stateText }
            }
            Text(sub, style = MaterialTheme.typography.bodySmall, color = if (on) Huis.colors.onLight else Huis.colors.muted)
            CommandStatusText(line)
        }
    }
}

@Composable
private fun PowerCard(power: LivePower, modifier: Modifier = Modifier) {
    HuisCard(modifier) {
        PowerSummary(power)
    }
}

/** Live power (P1) with quality per part; also used on the Energie tab. */
@Composable
fun PowerSummary(power: LivePower) {
    val quality = if (power.totalW == null) Quality.MISSING else Quality.MEASURED
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            stringResource(R.string.power_now),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.weight(1f).semantics { heading() },
        )
        Text(
            stringResource(R.string.power_source, qualityText(quality), NlFormat.relative(power.at)),
            color = Huis.colors.muted,
            style = MaterialTheme.typography.bodySmall,
        )
    }
    Text(NlFormat.watts(power.totalW), style = MaterialTheme.typography.displaySmall)
    val parts = power.parts.filter { (it.watts ?: 0.0) > 0 }
    val total = parts.sumOf { it.watts ?: 0.0 }.takeIf { it > 0 }
    if (total != null) {
        Row(
            Modifier
                .fillMaxWidth()
                .height(8.dp)
                .clip(RoundedCornerShape(4.dp)),
            horizontalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            parts.forEach { p ->
                Box(
                    Modifier
                        .weight(((p.watts ?: 0.0) / total).toFloat().coerceAtLeast(0.01f))
                        .height(8.dp)
                        .background(roleColor(p.role)),
                )
            }
        }
    }
    power.parts.forEach { p ->
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.size(10.dp).clip(RoundedCornerShape(3.dp)).background(roleColor(p.role)))
            Text(p.label.ifBlank { p.role }, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            Text(
                NlFormat.watts(p.watts),
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                color = if (p.watts == null || p.stale) Huis.colors.dimmed else Huis.colors.ink,
            )
            QualityLabel(if (p.stale) Quality.MISSING else p.quality)
        }
    }
    if (!power.complete) {
        Text(
            power.note ?: stringResource(R.string.power_incomplete),
            color = Huis.colors.warn,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
fun roleColor(role: String): Color = when (role) {
    "airco" -> Huis.colors.cool
    "boiler" -> Huis.colors.boiler
    "grid", "grid_import" -> Huis.colors.ink
    "grid_export" -> Huis.colors.ok
    else -> Huis.colors.rest
}
