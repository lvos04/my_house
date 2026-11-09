package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.DayEntry
import nl.huis.smarthome.core.DayGroup
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.UnitLink
import nl.huis.smarthome.core.WeekSchedule
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.core.ZoneSchedule
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.CommandUi
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.UnitState
import nl.huis.smarthome.ui.ZoneControls
import nl.huis.smarthome.ui.ZoneUi
import nl.huis.smarthome.ui.components.CommandStatusText
import nl.huis.smarthome.ui.components.ConnectionBanner
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.InfoBox
import nl.huis.smarthome.ui.components.Pill
import nl.huis.smarthome.ui.components.QualityLabel
import nl.huis.smarthome.ui.components.ReadOnlyNote
import nl.huis.smarthome.ui.components.SectionTitle
import nl.huis.smarthome.ui.components.SetpointControl
import nl.huis.smarthome.ui.components.UnitNotLinked
import nl.huis.smarthome.ui.components.ZoneHintNote
import nl.huis.smarthome.ui.components.ZonePillView
import nl.huis.smarthome.ui.components.controlLabel
import nl.huis.smarthome.ui.components.durationText
import nl.huis.smarthome.ui.components.fanLabel
import nl.huis.smarthome.ui.components.modeLabel
import nl.huis.smarthome.ui.components.qualityText
import nl.huis.smarthome.ui.components.targetSpoken
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.noFontScale
import java.time.LocalDateTime
import java.time.ZoneId

@Composable
fun ClimateScreen(
    state: HomeState,
    vm: MainViewModel,
    selectedZoneId: String?,
    onSelectZone: (String) -> Unit,
    onBack: () -> Unit,
) {
    val zones = state.overview?.zones.orEmpty()
    val zone = zones.firstOrNull { it.id == selectedZoneId } ?: zones.firstOrNull()

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack, modifier = Modifier.size(48.dp)) {
                Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = stringResource(R.string.back))
            }
            Text(
                zone?.name ?: stringResource(R.string.nav_climate),
                style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.weight(1f).semantics { heading() },
            )
            if (zone != null) ZonePillView(zone)
        }
        ConnectionBanner(state, vm::retry)
        ReadOnlyNote(state)

        if (zones.size > 1 && zone != null) {
            ScrollableTabRow(
                selectedTabIndex = zones.indexOf(zone),
                containerColor = Huis.colors.ground,
                contentColor = Huis.colors.ink,
                edgePadding = 0.dp,
            ) {
                zones.forEach { z ->
                    Tab(
                        selected = z.id == zone.id,
                        onClick = { onSelectZone(z.id) },
                        text = { Text(z.name) },
                        selectedContentColor = Huis.colors.ink,
                        unselectedContentColor = Huis.colors.muted,
                    )
                }
            }
        }

        if (zone == null) {
            Text(stringResource(R.string.no_zones), color = Huis.colors.muted)
            return@Column
        }

        val dim = if (state.online) 1f else 0.55f
        val unit = ZoneUi.unitState(zone)
        val controls = ZoneUi.controls(zone, state)

        // Reloaded when the zone, its next change (a saved schedule changes it) or the connection changes.
        LaunchedEffect(zone.id, zone.nextChange, zone.profileId, state.online) {
            if (state.online) vm.loadSchedule(zone.id)
        }
        val schedule = state.schedules[zone.id]
        val timezone = state.overview?.home?.timezone

        if (unit == UnitState.NOT_LINKED) {
            HuisCard(radius = 20) {
                if (ZoneUi.hasSensor(zone)) RoomReading(zone, Modifier.alpha(dim))
                UnitNotLinked(zone, canManage = state.isAdmin)
            }
            DetailsCard(zone, showFan = false, showMode = false)
            WeekScheduleCard(schedule, timezone, state.online, state.isAdmin)
            return@Column
        }

        val shown = state.shownTarget(zone)
        Dial(zone, shown, Modifier.align(Alignment.CenterHorizontally).alpha(dim))
        RoomReading(zone, Modifier.align(Alignment.CenterHorizontally).alpha(dim), centered = true)

        if (controls.visible) {
            Box(Modifier.align(Alignment.CenterHorizontally)) {
                SetpointControl(shown, controls.setpointEnabled, { vm.nudge(zone, it) }, zone.capabilities.stepC, big = true)
            }
        }
        CommandStatusText(CommandUi.line(state, zone), Modifier.align(Alignment.CenterHorizontally))
        ZoneHintNote(controls.hint)
        zone.notice?.takeIf { unit != UnitState.OFFLINE }?.let { InfoBox(it, warn = zone.noticeIsWarning) }

        if (controls.visible) {
            ModeSelector(zone, controls) { vm.setMode(zone, it) }
            if (zone.blockedModes.isNotEmpty()) {
                val lines = zone.blockedModes.map { b -> blockedLine(b.mode, b.reason) }
                InfoBox(lines.joinToString("\n"))
            }
            if (zone.capabilities.fan) FanSelector(zone, controls) { vm.setFan(zone, it) }
        }

        DetailsCard(zone, showFan = !controls.visible || !zone.capabilities.fan, showMode = !controls.visible)
        WeekScheduleCard(schedule, timezone, state.online, state.isAdmin)

        if (controls.visible) {
            if (controls.showResume) {
                OutlinedButton(
                    onClick = { vm.resume(zone) },
                    enabled = controls.resumeEnabled,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                    shape = RoundedCornerShape(16.dp),
                    border = BorderStroke(1.dp, Huis.colors.controlOutline),
                ) { Text(stringResource(R.string.resume_schedule), color = Huis.colors.ink) }
            } else {
                val minutes = ZoneUi.overrideMinutes(zone)
                Button(
                    onClick = { vm.overrideFor(zone, minutes) },
                    enabled = controls.overrideEnabled,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                    shape = RoundedCornerShape(16.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Huis.colors.ink, contentColor = Huis.colors.ground),
                ) { Text(stringResource(R.string.override_for, durationText(minutes))) }
            }
        }
    }
}

@Composable
private fun blockedLine(mode: String, reason: String): String =
    stringResource(R.string.mode_unavailable, modeLabel(mode), reason)

/** The target in a ring (heat orange, cool blue). The number is read aloud when it changes. */
@Composable
private fun Dial(zone: Zone, target: Double?, modifier: Modifier = Modifier) {
    val off = zone.mode == "off" || zone.unitLink == UnitLink.OFFLINE
    val ring = when {
        off -> Huis.colors.outline
        zone.mode == "heat" -> Huis.colors.heatRing
        zone.mode == "cool" -> Huis.colors.cool.copy(alpha = 0.35f)
        else -> Huis.colors.outline
    }
    val accent = when (zone.mode) {
        "heat" -> Huis.colors.heat
        "cool" -> Huis.colors.cool
        else -> Huis.colors.muted
    }
    val caps = zone.capabilities
    val span = (caps.maxC - caps.minC).takeIf { it > 0 } ?: 1.0
    val fraction = target?.let { ((it - caps.minC) / span).toFloat().coerceIn(0f, 1f) }
    val surface = Huis.colors.surface
    val spoken = if (zone.mode == "off") modeLabel("off") else targetSpoken(target)
    Box(modifier.size(240.dp), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(240.dp)) {
            val stroke = 12.dp.toPx()
            val inset = stroke / 2
            val arcSize = Size(size.width - stroke, size.height - stroke)
            drawCircle(surface, radius = size.minDimension / 2 - stroke)
            drawArc(ring, startAngle = 135f, sweepAngle = 270f, useCenter = false, topLeft = Offset(inset, inset), size = arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
            if (fraction != null && !off) {
                drawArc(accent, startAngle = 135f, sweepAngle = 270f * fraction, useCenter = false, topLeft = Offset(inset, inset), size = arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
            }
        }
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier
                .padding(horizontal = 28.dp)
                .clearAndSetSemantics {
                    contentDescription = spoken
                    liveRegion = LiveRegionMode.Polite
                },
        ) {
            Text(stringResource(R.string.target), color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium)
            // The ring has a fixed size, so the number keeps its size at large font settings
            // (it is already 60 sp); every other text scales normally.
            Text(
                if (zone.mode == "off") modeLabel("off") else NlFormat.temperature(target),
                style = MaterialTheme.typography.displayLarge.noFontScale(),
                maxLines = 1,
            )
        }
    }
}

/** "nu 20,3° · gemeten · 2 min geleden" (or "Sensor verouderd"). */
@Composable
private fun RoomReading(zone: Zone, modifier: Modifier = Modifier, centered: Boolean = false) {
    val t = zone.temperature
    Column(modifier, horizontalAlignment = if (centered) Alignment.CenterHorizontally else Alignment.Start) {
        if (!centered) {
            Text(
                NlFormat.temperature(t.value),
                style = MaterialTheme.typography.displayMedium.noFontScale(),
                color = if (t.stale || t.value == null) Huis.colors.dimmed else Huis.colors.ink,
            )
        }
        val line = listOfNotNull(
            if (centered) stringResource(R.string.now_short, NlFormat.temperature(t.value)) else null,
            if (t.stale) stringResource(R.string.stale_sensor_since, NlFormat.relative(t.at)) else qualityText(t.quality),
            t.at?.takeIf { !t.stale }?.let { NlFormat.relative(it) },
        ).joinToString(" · ")
        Text(
            line,
            color = if (t.stale) Huis.colors.warn else Huis.colors.muted,
            style = MaterialTheme.typography.bodyMedium,
            textAlign = if (centered) TextAlign.Center else TextAlign.Start,
        )
    }
}

/** Options per row: fewer at large font settings so labels never get cut off. */
@Composable
private fun perRow(normal: Int): Int = if (LocalDensity.current.fontScale > 1.3f) 2 else normal

@Composable
private fun ModeSelector(zone: Zone, controls: ZoneControls, onPick: (String) -> Unit) {
    SectionTitle(stringResource(R.string.mode))
    val modes = ZoneUi.modes(zone)
    Column(Modifier.fillMaxWidth().selectableGroup(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        modes.chunked(perRow(3)).forEach { row ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { m ->
                    val reason = zone.blockReason(m)
                    val blockedLabel = reason?.let { blockedLine(m, it) }
                    ChoiceButton(
                        label = modeLabel(m),
                        selected = zone.mode == m,
                        enabled = controls.modesEnabled && reason == null,
                        accent = m,
                        stateExtra = blockedLabel,
                        modifier = Modifier.weight(1f),
                    ) { if (zone.mode != m) onPick(m) }
                }
                repeat(perRow(3) - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}

@Composable
private fun FanSelector(zone: Zone, controls: ZoneControls, onPick: (String) -> Unit) {
    SectionTitle(stringResource(R.string.fan))
    Column(Modifier.fillMaxWidth().selectableGroup(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        ZoneUi.FAN_SPEEDS.chunked(perRow(4)).forEach { row ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { f ->
                    ChoiceButton(
                        label = fanLabel(f),
                        selected = zone.fan == f,
                        enabled = controls.fanEnabled,
                        accent = "fan",
                        stateExtra = null,
                        modifier = Modifier.weight(1f),
                    ) { if (zone.fan != f) onPick(f) }
                }
                repeat(perRow(4) - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}

/** A radio-style choice (mode or fan speed), at least 52 dp high and growing with the text. */
@Composable
private fun ChoiceButton(
    label: String,
    selected: Boolean,
    enabled: Boolean,
    accent: String,
    stateExtra: String?,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val (bg, fg) = when {
        selected && accent == "heat" -> Huis.colors.heatContainer to Huis.colors.heat
        selected && accent == "cool" -> Huis.colors.coolContainer to Huis.colors.cool
        selected -> Huis.colors.surfaceMuted to Huis.colors.ink
        else -> Huis.colors.surface to Huis.colors.ink
    }
    val border = when {
        selected && accent == "heat" -> BorderStroke(2.dp, Huis.colors.heat)
        selected && accent == "cool" -> BorderStroke(2.dp, Huis.colors.cool)
        selected -> BorderStroke(2.dp, Huis.colors.ink)
        else -> BorderStroke(1.dp, Huis.colors.controlOutline)
    }
    Surface(
        modifier = modifier
            .heightIn(min = 52.dp)
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onClick)
            .semantics { if (stateExtra != null) stateDescription = stateExtra }
            .alpha(if (enabled || selected) 1f else 0.5f),
        shape = RoundedCornerShape(16.dp),
        color = bg,
        border = border,
    ) {
        Box(contentAlignment = Alignment.Center, modifier = Modifier.padding(horizontal = 6.dp, vertical = 14.dp)) {
            Text(
                label,
                color = fg,
                textAlign = TextAlign.Center,
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium),
            )
        }
    }
}

@Composable
private fun DetailsCard(zone: Zone, showFan: Boolean, showMode: Boolean) {
    HuisCard(radius = 20) {
        if (showMode) InfoRow(stringResource(R.string.mode), modeLabel(zone.mode))
        InfoRow(stringResource(R.string.control), if (zone.unitLinked) controlLabel(zone) else stringResource(R.string.zone_no_unit_pill))
        zone.profile?.let { InfoRow(stringResource(R.string.profile), it) }
        zone.nextChange?.let { n ->
            InfoRow(
                stringResource(R.string.next_change),
                listOfNotNull(NlFormat.clock(n.at), n.targetC?.let { NlFormat.temperature(it) }, n.mode?.let { modeLabel(it) })
                    .joinToString(" · "),
            )
        }
        if (showFan) zone.fan?.let { InfoRow(stringResource(R.string.fan), fanLabel(it)) }
        zone.humidity.value?.let {
            InfoRow(stringResource(R.string.humidity), NlFormat.percent(it), quality = { QualityLabel(zone.humidity.quality) })
        }
    }
}

/**
 * The zone's week schedule, read-only (verbeterlijst 12): days with the same blocks together
 * ("ma–vr"), the block in force marked "nu". Editing happens in the web app (Klimaat).
 */
@Composable
private fun WeekScheduleCard(schedule: ZoneSchedule?, timezone: String?, online: Boolean, isAdmin: Boolean) {
    HuisCard(radius = 20) {
        SectionTitle(stringResource(R.string.schedule_title))
        when {
            schedule == null -> Text(
                stringResource(if (online) R.string.schedule_loading else R.string.schedule_offline),
                color = Huis.colors.muted,
                style = MaterialTheme.typography.bodyMedium,
            )
            schedule.blocks.isEmpty() -> InfoBox(stringResource(R.string.schedule_empty), warn = true)
            else -> {
                schedule.profile?.let {
                    Text(stringResource(R.string.schedule_profile, it), color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium)
                }
                val zoneId = remember(timezone) { runCatching { ZoneId.of(timezone ?: "Europe/Amsterdam") }.getOrDefault(ZoneId.of("Europe/Amsterdam")) }
                val active = WeekSchedule.active(schedule.blocks, LocalDateTime.now(zoneId))
                WeekSchedule.groups(schedule.blocks).forEach { group ->
                    ScheduleDayGroup(group, active?.takeIf { it.day in group.days }?.entry)
                }
            }
        }
        Text(
            stringResource(if (isAdmin) R.string.schedule_edit_hint else R.string.schedule_edit_hint_other),
            color = Huis.colors.muted,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun ScheduleDayGroup(group: DayGroup, active: DayEntry?) {
    val spokenDays = WeekSchedule.daysLabel(group.days, long = true)
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            WeekSchedule.daysLabel(group.days),
            style = MaterialTheme.typography.titleSmall,
            color = Huis.colors.ink,
            modifier = Modifier.semantics { contentDescription = spokenDays },
        )
        if (group.entries.isEmpty()) {
            Text(stringResource(R.string.schedule_no_blocks), color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium)
        }
        val nowText = stringResource(R.string.schedule_now_spoken)
        group.entries.forEach { e ->
            val isActive = e == active
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 32.dp)
                    .semantics(mergeDescendants = true) { if (isActive) stateDescription = nowText },
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(
                    e.start,
                    style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                    color = Huis.colors.ink,
                )
                Text(
                    listOf(
                        NlFormat.temperature(e.targetC),
                        e.mode?.let { modeLabel(it) } ?: stringResource(R.string.schedule_keep_mode),
                        e.label.takeIf { it.isNotBlank() },
                    ).filterNotNull().joinToString(" · "),
                    style = MaterialTheme.typography.bodyMedium,
                    color = Huis.colors.ink,
                    modifier = Modifier.weight(1f),
                )
                if (isActive) Pill(stringResource(R.string.schedule_now), Huis.colors.coolContainer, Huis.colors.onCoolContainer)
            }
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String, quality: (@Composable () -> Unit)? = null) {
    Column {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1.2f, fill = false), horizontalAlignment = Alignment.End) {
                Text(value, style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold), textAlign = TextAlign.End)
                quality?.invoke()
            }
        }
        HorizontalDivider(color = Huis.colors.outline)
    }
}
