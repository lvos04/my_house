package nl.huis.smarthome.ui.screens

import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
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
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedIconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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
import nl.huis.smarthome.ui.Attention
import nl.huis.smarthome.ui.AttentionLevel
import nl.huis.smarthome.ui.AttentionUi
import nl.huis.smarthome.ui.BannerKind
import nl.huis.smarthome.ui.CommandLine
import nl.huis.smarthome.ui.CommandUi
import nl.huis.smarthome.ui.ConnectionUi
import nl.huis.smarthome.ui.LightsUi
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.NotifPrompt
import nl.huis.smarthome.ui.NotifUi
import nl.huis.smarthome.ui.SystemLine
import nl.huis.smarthome.ui.SystemLineUi
import nl.huis.smarthome.ui.UnitState
import nl.huis.smarthome.ui.ZoneUi
import nl.huis.smarthome.ui.PeilStatus
import nl.huis.smarthome.ui.PeilUi
import nl.huis.smarthome.ui.components.Aanraakdoel
import nl.huis.smarthome.ui.components.CommandStatusText
import nl.huis.smarthome.ui.components.PeilIcoon
import nl.huis.smarthome.ui.components.SectionTitle
import nl.huis.smarthome.ui.components.StatusBadge
import nl.huis.smarthome.ui.components.arcering
import nl.huis.smarthome.ui.components.lastKnownClock
import nl.huis.smarthome.ui.components.stippelRand
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
import nl.huis.smarthome.ui.components.relativeText
import nl.huis.smarthome.ui.components.textSource
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.LocalHuisKlok
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
    // Offline: no dimming (Peil: never opacity on text); every card is marked "Verouderd · Laatst bekend · 15:47".
    val stale = if (state.online) null else lastKnownClock(state)
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item(key = "header") {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            overview?.home?.name ?: state.pairing?.homeName ?: "",
                            color = Huis.colors.muted,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.weight(1f, fill = false),
                        )
                        RouteChip(state)
                    }
                    Text(
                        greeting(),
                        style = MaterialTheme.typography.headlineSmall,
                        color = Huis.colors.ink,
                        modifier = Modifier.semantics { heading() },
                    )
                }
                val openAlerts = overview?.alerts?.count { !it.acknowledged } ?: 0
                val label = stringResource(R.string.alerts_button, openAlerts)
                OutlinedIconButton(
                    onClick = onOpenAlerts,
                    modifier = Modifier.size(Aanraakdoel).semantics { contentDescription = label },
                    border = BorderStroke(1.dp, Huis.colors.controlOutline),
                    colors = IconButtonDefaults.outlinedIconButtonColors(containerColor = Huis.colors.surface),
                ) {
                    // The count in ink, never red: the number is information, the alerts say how serious they are.
                    BadgedBox(badge = { if (openAlerts > 0) Badge(containerColor = Huis.colors.ink, contentColor = Huis.colors.surface) { Text("$openAlerts") } }) {
                        PeilIcoon(R.drawable.lucide_bell, Huis.colors.ink, size = 24.dp)
                    }
                }
            }
        }
        if (ConnectionUi.banner(state).kind != BannerKind.NONE) {
            item(key = "banner") { ConnectionBanner(state, vm::retry) }
        }
        if (state.readOnly) item(key = "readonly") { ReadOnlyNote(state) }
        // Always there with an overview, also when all is well; problems get the block below too.
        val systemLine = SystemLineUi.of(overview?.status.orEmpty())
        if (overview != null && !systemLine.isEmpty) {
            item(key = "system") { SystemStatusLine(systemLine) }
        }
        val attention = AttentionUi.of(overview?.status.orEmpty())
        if (attention.level != AttentionLevel.NONE) {
            item(key = "attention") { AttentionCard(attention, isAdmin = state.isAdmin) }
        }
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
                stale = stale,
                onNudge = { vm.nudge(zone, it) },
                onOpen = { onOpenZone(zone.id) },
            )
        }

        val lights = LightsUi.visible(overview?.lights.orEmpty())
        if (lights.isNotEmpty()) {
            item(key = "lights-title") { SectionTitle(stringResource(R.string.lights)) }
            items(lights.chunked(2), key = { row -> row.joinToString("|") { it.id } }) { row ->
                // Both tiles of a row equally high (one may carry the Onbekend badge).
                Row(Modifier.height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    row.forEach { g ->
                        LightTile(
                            group = g,
                            line = CommandUi.line(state, g),
                            enabled = state.controlsEnabled,
                            readOnly = state.readOnly,
                            stale = stale != null,
                            modifier = Modifier.weight(1f).fillMaxHeight(),
                        ) { vm.toggle(g) }
                    }
                    if (row.size == 1) Box(Modifier.weight(1f))
                }
            }
        }

        overview?.powerNow?.let { power -> item(key = "power") { HuisCard(verouderd = stale) { PowerSummary(power, stale = stale != null) } } }
    }
}

/**
 * "Laatste back-up 5 uur geleden · Achtergronddienst actief" (+ "Veilige modus aan"): one quiet
 * line with its Peil status (Gezond, Beperkt or Storing: icon + word), so the user also sees that
 * backups and the worker run when nothing is wrong.
 */
@Composable
private fun SystemStatusLine(line: SystemLine, modifier: Modifier = Modifier) {
    Column(
        modifier.fillMaxWidth().semantics(mergeDescendants = true) {},
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        StatusBadge(PeilUi.systeem(line.level))
        Text(
            SystemLineUi.text(textSource(), line),
            color = Huis.colors.muted,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

/**
 * "Wat actie nodig heeft" (verbeterlijst 22; same words as the Hub): worker heartbeat, safe mode,
 * last backup and maintenance. A panel with a 2 dp border in the worst status; every item has its
 * status icon and word, so colour is never the only sign.
 */
@Composable
private fun AttentionCard(attention: Attention, isAdmin: Boolean) {
    val serious = attention.level == AttentionLevel.ERROR
    val p = Huis.peil
    val (vlak, rand) = if (serious) p.downBg to p.down else p.degradedBg to p.degraded
    val inkt = if (serious) p.downInk else p.degradedInk
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(vlak)
            .border(BorderStroke(2.dp, rand), RoundedCornerShape(8.dp))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            PeilIcoon(if (serious) R.drawable.lucide_octagon_x else R.drawable.lucide_triangle_alert, rand, size = 22.dp)
            Text(
                stringResource(if (serious) R.string.attention_title_error else R.string.attention_title_warn),
                style = MaterialTheme.typography.titleMedium,
                color = inkt,
                modifier = Modifier.semantics { heading() },
            )
        }
        attention.items.forEach { item ->
            Column(
                Modifier.fillMaxWidth().semantics(mergeDescendants = true) {},
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusBadge(PeilUi.item(item.state))
                    Text(
                        item.label.ifBlank { item.key },
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                        color = inkt,
                        modifier = Modifier.weight(1f),
                    )
                }
                item.detail?.takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = inkt)
                }
            }
        }
        Text(
            stringResource(if (isAdmin) R.string.attention_more_admin else R.string.attention_more),
            style = MaterialTheme.typography.bodySmall,
            color = inkt,
        )
    }
}

@Composable
private fun greeting(): String {
    val h = LocalTime.now(LocalHuisKlok.current).hour
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
    stale: String?,
    onNudge: (Double) -> Unit,
    onOpen: () -> Unit,
) {
    val unit = ZoneUi.unitState(zone)
    val controls = ZoneUi.controls(zone, state)
    val openLabel = stringResource(R.string.zone_open, zone.name)
    HuisCard(onClick = onOpen, onClickLabel = openLabel, verouderd = stale) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(zone.name, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            ZonePillView(zone)
        }
        if (unit == UnitState.NOT_LINKED) {
            if (ZoneUi.hasSensor(zone)) Reading(zone, stale = stale != null)
            UnitNotLinked(zone, canManage = state.isAdmin)
            return@HuisCard
        }
        val shown = state.shownTarget(zone)
        val target: @Composable () -> Unit = {
            when {
                controls.visible && unit == UnitState.ON ->
                    SetpointControl(value = shown, enabled = controls.setpointEnabled, onNudge = onNudge, step = zone.capabilities.stepC)
                unit == UnitState.ON -> TargetReadout(shown)
                else -> Unit
            }
        }
        if (LocalDensity.current.fontScale > 1.3f) {
            // Large font: reading and +/− below each other, so neither gets squeezed.
            Reading(zone, stale = stale != null)
            target()
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Reading(zone, Modifier.weight(1f), stale = stale != null)
                target()
            }
        }
        when (unit) {
            UnitState.OFF -> Text(stringResource(R.string.zone_off_short), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
            UnitState.OFFLINE -> IconLine(R.drawable.lucide_circle_help, Huis.peil.unknown, stringResource(R.string.unit_unreachable), Huis.peil.unknownInk)
            else -> Unit
        }
        zone.notice?.takeIf { zone.noticeIsWarning && unit == UnitState.ON }?.let {
            IconLine(R.drawable.lucide_triangle_alert, Huis.peil.degraded, it, Huis.peil.degradedInk)
        }
        CommandStatusText(CommandUi.line(state, zone))
    }
}

/** A short line with its icon (18 dp) in front: the icon carries the meaning, the colour only helps. */
@Composable
private fun IconLine(@DrawableRes icoon: Int, iconTint: Color, text: String, textColor: Color) {
    Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        PeilIcoon(icoon, iconTint, Modifier.padding(top = 1.dp), size = 18.dp)
        Text(text, color = textColor, style = MaterialTheme.typography.bodySmall)
    }
}

/** Room temperature with its quality label (or "Sensor verouderd"); last known values in stale ink. */
@Composable
private fun Reading(zone: Zone, modifier: Modifier = Modifier, stale: Boolean = false) {
    val t = zone.temperature
    Column(modifier, verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(
            NlFormat.temperature(t.value),
            style = MaterialTheme.typography.displaySmall,
            color = if (stale || t.stale || t.value == null) Huis.colors.dimmed else Huis.colors.ink,
        )
        if (t.stale) {
            IconLine(R.drawable.lucide_clock_alert, Huis.peil.stale, stringResource(R.string.stale_sensor_since, relativeText(t.at)), Huis.peil.staleInk)
        } else {
            QualityLabel(t.quality, t.at?.let { relativeText(it) })
        }
    }
}

/**
 * A light group (LichtTegel): one tap switches it. On = warm surface with a lightbulb, off = strong
 * border with lightbulb-off, unreachable = Onbekend (dashed, never transparent). TalkBack hears a
 * switch with its state ("aan"/"uit"); a viewer phone sees the state without a switch.
 */
@Composable
private fun LightTile(
    group: LightGroup,
    line: CommandLine?,
    enabled: Boolean,
    readOnly: Boolean,
    stale: Boolean,
    modifier: Modifier = Modifier,
    onToggle: () -> Unit,
) {
    val p = Huis.peil
    val on = group.on && group.reachable
    val shape = RoundedCornerShape(8.dp)
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
    val (vlak, inkt) = when {
        !group.reachable -> p.unknownBg to p.unknownInk
        on -> p.domainLightOnBg to p.domainLightOnInk
        else -> p.card to p.foreground
    }
    val rand = when {
        !group.reachable || stale -> Modifier.stippelRand(if (stale) p.stale else p.unknown, radius = 8.dp)
        // The ink, not the amber domain colour (2,8:1 on its own surface in light): ≥ 3:1 (PeilContrastTest).
        on -> Modifier.border(BorderStroke(2.dp, p.domainLightOnInk), shape)
        else -> Modifier.border(BorderStroke(1.dp, p.borderStrong), shape)
    }
    Column(
        modifier
            .heightIn(min = 76.dp)
            .clip(shape)
            .background(vlak)
            .then(interaction)
            .then(rand)
            .padding(horizontal = 12.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            PeilIcoon(
                if (on) R.drawable.lucide_lightbulb else R.drawable.lucide_lightbulb_off,
                if (stale) p.staleInk else inkt,
                size = 20.dp,
            )
            Text(group.name, style = MaterialTheme.typography.titleSmall, color = if (stale) p.staleInk else inkt)
        }
        if (!group.reachable) {
            StatusBadge(PeilStatus.ONBEKEND)
            Text(stateText, style = MaterialTheme.typography.bodySmall, color = inkt)
        } else {
            val sub = when {
                !group.on -> stateText
                else -> listOfNotNull(
                    group.scene,
                    group.brightness?.let { "$it%" },
                    group.lightsTotal.takeIf { it > 1 && group.lightsOn in 1 until it }?.let { stringResource(R.string.lights_partly, group.lightsOn, it) },
                ).joinToString(" · ").ifBlank { stateText }
            }
            Text(sub, style = MaterialTheme.typography.bodySmall, color = if (stale) p.staleInk else inkt)
        }
        CommandStatusText(line)
    }
}

/**
 * Live power (P1) with quality per part; also used on the Energie tab. The thin bar (Peil
 * "dunne segmentbalk") uses the chart colours: airco chart-1, boiler chart-3, overig chart-other
 * with hatching, teruglevering chart-5; the list below carries the numbers.
 */
@Composable
fun PowerSummary(power: LivePower, stale: Boolean = false) {
    val quality = if (power.totalW == null) Quality.MISSING else Quality.MEASURED
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            stringResource(R.string.power_now),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.weight(1f).semantics { heading() },
        )
        Text(
            stringResource(R.string.power_source, qualityText(quality), relativeText(power.at)),
            color = Huis.colors.muted,
            style = MaterialTheme.typography.bodySmall,
        )
    }
    Text(
        NlFormat.watts(power.totalW),
        style = MaterialTheme.typography.displaySmall,
        color = if (stale) Huis.colors.dimmed else Huis.colors.ink,
    )
    val parts = power.parts.filter { (it.watts ?: 0.0) > 0 }
    val total = parts.sumOf { it.watts ?: 0.0 }.takeIf { it > 0 }
    if (total != null) {
        val card = Huis.peil.card
        val hatch = Huis.peil.card
        Row(
            Modifier
                .fillMaxWidth()
                .height(8.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(card),
            horizontalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            parts.forEach { p ->
                Box(
                    Modifier
                        .weight(((p.watts ?: 0.0) / total).toFloat().coerceAtLeast(0.01f))
                        .height(8.dp)
                        .background(roleColor(p.role))
                        .then(if (p.role == "rest") Modifier.arcering(hatch, spacing = 4.dp) else Modifier),
                )
            }
        }
    }
    power.parts.forEach { p ->
        Row(
            Modifier.heightIn(min = 32.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            RoleSwatch(p.role)
            Text(p.label.ifBlank { p.role }, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            Text(
                NlFormat.watts(p.watts),
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                color = if (stale || p.watts == null || p.stale) Huis.colors.dimmed else Huis.colors.ink,
            )
            QualityLabel(if (p.stale) Quality.MISSING else p.quality)
        }
    }
    if (!power.complete) {
        IconLine(R.drawable.lucide_info, Huis.peil.mutedForeground, power.note ?: stringResource(R.string.power_incomplete), Huis.peil.mutedForeground)
    }
}

/** Colour square of a role in a legend or list (overig hatched, as in the bars). */
@Composable
fun RoleSwatch(role: String, modifier: Modifier = Modifier) {
    val hatch = Huis.peil.card
    Box(
        modifier
            .size(12.dp)
            .clip(RoundedCornerShape(3.dp))
            .background(roleColor(role))
            .then(if (role == "rest") Modifier.arcering(hatch, spacing = 3.dp) else Modifier),
    )
}

/** Chart colours per meter role (Peil palette, validated in light and dark). */
@Composable
fun roleColor(role: String): Color = when (role) {
    "airco" -> Huis.peil.chart1
    "boiler" -> Huis.peil.chart3
    "grid", "grid_import" -> Huis.peil.chartLimit
    "grid_export" -> Huis.peil.chart5
    else -> Huis.peil.chartOther
}
