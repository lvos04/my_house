package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
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
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
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
import androidx.compose.ui.unit.Dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.DayEntry
import nl.huis.smarthome.core.DayGroup
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.UnitLink
import nl.huis.smarthome.core.WeekSchedule
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.core.ZoneSchedule
import nl.huis.smarthome.data.CoreTexts
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.CommandUi
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.MeldingSoort
import nl.huis.smarthome.ui.UnitState
import nl.huis.smarthome.ui.ZoneControls
import nl.huis.smarthome.ui.ZoneUi
import nl.huis.smarthome.ui.components.CommandStatusText
import nl.huis.smarthome.ui.components.Aanraakdoel
import nl.huis.smarthome.ui.components.KnopSoort
import nl.huis.smarthome.ui.components.Note
import nl.huis.smarthome.ui.components.PeilIcoon
import nl.huis.smarthome.ui.components.PeilKnop
import nl.huis.smarthome.ui.components.VerouderdRegel
import nl.huis.smarthome.ui.components.lastKnownClock
import nl.huis.smarthome.ui.components.stippelRand
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
import nl.huis.smarthome.ui.components.relativeText
import nl.huis.smarthome.ui.components.targetSpoken
import nl.huis.smarthome.ui.components.textSource
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.LocalHuisKlok
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
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            val back = stringResource(R.string.back)
            IconButton(onClick = onBack, modifier = Modifier.size(Aanraakdoel).semantics { contentDescription = back }) {
                PeilIcoon(R.drawable.lucide_arrow_left, Huis.colors.ink, size = 24.dp)
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
            // Peil tabs: the selected tab is bold with a 4 dp underline in primary blue (not colour alone).
            val selectedIndex = zones.indexOf(zone)
            ScrollableTabRow(
                selectedTabIndex = selectedIndex,
                containerColor = Huis.colors.ground,
                contentColor = Huis.colors.ink,
                edgePadding = 0.dp,
                indicator = { positions ->
                    if (selectedIndex in positions.indices) {
                        TabRowDefaults.SecondaryIndicator(
                            Modifier.tabIndicatorOffset(positions[selectedIndex]),
                            height = 4.dp,
                            color = Huis.peil.primary,
                        )
                    }
                },
                divider = { HorizontalDivider(color = Huis.peil.border) },
            ) {
                zones.forEach { z ->
                    val selected = z.id == zone.id
                    Tab(
                        selected = selected,
                        onClick = { onSelectZone(z.id) },
                        modifier = Modifier.heightIn(min = Aanraakdoel),
                        text = {
                            Text(
                                z.name,
                                style = MaterialTheme.typography.bodyLarge.copy(fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium),
                            )
                        },
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

        // Offline: no dimming (Peil); the values are marked "Verouderd · Laatst bekend · 15:47".
        val stale = if (state.online) null else lastKnownClock(state)
        val unit = ZoneUi.unitState(zone)
        val controls = ZoneUi.controls(zone, state)

        // Reloaded when the zone, its next change (a saved schedule changes it) or the connection changes.
        LaunchedEffect(zone.id, zone.nextChange, zone.profileId, state.online) {
            if (state.online) vm.loadSchedule(zone.id)
        }
        val schedule = state.schedules[zone.id]
        val timezone = state.overview?.home?.timezone

        if (unit == UnitState.NOT_LINKED) {
            HuisCard(verouderd = stale) {
                if (ZoneUi.hasSensor(zone)) RoomReading(zone, stale = stale != null)
                UnitNotLinked(zone, canManage = state.isAdmin)
            }
            DetailsCard(zone, showFan = false, showMode = false)
            WeekScheduleCard(schedule, timezone, state.online, state.isAdmin)
            return@Column
        }

        val shown = state.shownTarget(zone)
        stale?.let { VerouderdRegel(it, Modifier.align(Alignment.CenterHorizontally)) }
        Dial(zone, shown, Modifier.align(Alignment.CenterHorizontally), stale = stale != null)
        RoomReading(zone, Modifier.align(Alignment.CenterHorizontally), centered = true, stale = stale != null)

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
                // A rule of the installation (shared outdoor unit), not a fault: the gate shape with a lock.
                val lines = zone.blockedModes.map { b -> blockedLine(b.mode, b.reason) }
                Note(lines.joinToString("\n"), MeldingSoort.GATE)
            }
            if (zone.capabilities.fan) FanSelector(zone, controls) { vm.setFan(zone, it) }
        }

        DetailsCard(zone, showFan = !controls.visible || !zone.capabilities.fan, showMode = !controls.visible)
        WeekScheduleCard(schedule, timezone, state.online, state.isAdmin)

        if (controls.visible) {
            if (controls.showResume) {
                PeilKnop(
                    stringResource(R.string.resume_schedule),
                    onClick = { vm.resume(zone) },
                    enabled = controls.resumeEnabled,
                    soort = KnopSoort.PRIMAIR,
                    modifier = Modifier.fillMaxWidth(),
                )
            } else {
                val minutes = ZoneUi.overrideMinutes(zone)
                PeilKnop(
                    stringResource(R.string.override_for, durationText(minutes)),
                    onClick = { vm.overrideFor(zone, minutes) },
                    enabled = controls.overrideEnabled,
                    soort = KnopSoort.SECUNDAIR,
                    icoon = R.drawable.lucide_hand,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@Composable
private fun blockedLine(mode: String, reason: String): String =
    stringResource(R.string.mode_unavailable, modeLabel(mode), reason)

/**
 * The target in a ring: the track in the soft domain colour (heat or cool), the filled part in the
 * domain colour, nothing filled when off. The number is read aloud when it changes.
 */
@Composable
private fun Dial(zone: Zone, target: Double?, modifier: Modifier = Modifier, stale: Boolean = false) {
    val p = Huis.peil
    val off = zone.mode == "off" || zone.unitLink == UnitLink.OFFLINE
    val ring = when {
        off -> p.chartGap
        zone.mode == "heat" -> p.domainHeatSoft
        zone.mode == "cool" -> p.domainCoolSoft
        else -> p.chartGap
    }
    val accent = when (zone.mode) {
        "heat" -> p.domainHeat
        "cool" -> p.domainCool
        else -> p.mutedForeground
    }
    val caps = zone.capabilities
    val span = (caps.maxC - caps.minC).takeIf { it > 0 } ?: 1.0
    val fraction = target?.let { ((it - caps.minC) / span).toFloat().coerceIn(0f, 1f) }
    val surface = p.card
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
            // (Peil "held", 48 sp); every other text scales normally.
            Text(
                if (zone.mode == "off") modeLabel("off") else NlFormat.temperature(target),
                style = MaterialTheme.typography.displayLarge.noFontScale(),
                color = if (stale) Huis.colors.dimmed else Huis.colors.ink,
                maxLines = 1,
            )
        }
    }
}

/** "nu 20,3° · gemeten · 2 min geleden" (or "Sensor verouderd"), never dimmed. */
@Composable
private fun RoomReading(zone: Zone, modifier: Modifier = Modifier, centered: Boolean = false, stale: Boolean = false) {
    val t = zone.temperature
    Column(modifier, horizontalAlignment = if (centered) Alignment.CenterHorizontally else Alignment.Start) {
        if (!centered) {
            Text(
                NlFormat.temperature(t.value),
                style = MaterialTheme.typography.displaySmall,
                color = if (stale || t.stale || t.value == null) Huis.colors.dimmed else Huis.colors.ink,
            )
        }
        val line = listOfNotNull(
            if (centered) stringResource(R.string.now_short, NlFormat.temperature(t.value)) else null,
            if (t.stale) stringResource(R.string.stale_sensor_since, relativeText(t.at)) else qualityText(t.quality),
            t.at?.takeIf { !t.stale }?.let { relativeText(it) },
        ).joinToString(" · ")
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            if (t.stale) PeilIcoon(R.drawable.lucide_clock_alert, Huis.peil.stale, Modifier.padding(top = 2.dp), size = 18.dp)
            Text(
                line,
                color = if (t.stale) Huis.peil.staleInk else Huis.colors.muted,
                style = MaterialTheme.typography.bodyMedium,
                textAlign = if (centered) TextAlign.Center else TextAlign.Start,
            )
        }
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
            // Same height for every button in a row (a chosen one has its check above the label).
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { m ->
                    val reason = zone.blockReason(m)
                    val blockedLabel = reason?.let { blockedLine(m, it) }
                    ChoiceButton(
                        label = modeLabel(m),
                        selected = zone.mode == m,
                        enabled = controls.modesEnabled && reason == null,
                        accent = m,
                        blocked = reason != null,
                        stateExtra = blockedLabel,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
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
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { f ->
                    ChoiceButton(
                        label = fanLabel(f),
                        selected = zone.fan == f,
                        enabled = controls.fanEnabled,
                        accent = "fan",
                        blocked = false,
                        stateExtra = null,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    ) { if (zone.fan != f) onPick(f) }
                }
                repeat(perRow(4) - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}

/**
 * A radio-style choice (mode or fan speed), at least 52 dp high and growing with the text (Peil
 * ToggleGroup). Selected = 2 dp border + check icon above the label (heat and cool in their domain colours);
 * blocked by a rule = gate shape with a lock; disabled = dashed border, never transparent.
 */
@Composable
private fun ChoiceButton(
    label: String,
    selected: Boolean,
    enabled: Boolean,
    accent: String,
    blocked: Boolean,
    stateExtra: String?,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val p = Huis.peil
    val shape = RoundedCornerShape(8.dp)
    data class Look(val bg: Color, val fg: Color, val border: Color, val width: Dp, val dashed: Boolean)
    val look = when {
        // Border in the domain ink, not the domain colour: ≥ 3:1 against the surface around it (PeilContrastTest).
        selected && accent == "heat" -> Look(p.domainHeatBg, p.domainHeatInk, p.domainHeatInk, 2.dp, false)
        selected && accent == "cool" -> Look(p.domainCoolBg, p.domainCoolInk, p.domainCoolInk, 2.dp, false)
        selected -> Look(p.accent, p.foreground, p.foreground, 2.dp, false)
        blocked -> Look(p.gateBg, p.gateInk, p.gate, 2.dp, true)
        !enabled -> Look(p.muted, p.disabledForeground, p.borderStrong, 1.5.dp, true)
        else -> Look(p.card, p.foreground, p.borderStrong, 1.dp, false)
    }
    val icoon = when {
        selected -> R.drawable.lucide_check
        blocked -> R.drawable.lucide_lock
        else -> null
    }
    Box(
        modifier = modifier
            .heightIn(min = 52.dp)
            .clip(shape)
            .background(look.bg)
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onClick)
            .semantics { if (stateExtra != null) stateDescription = stateExtra }
            .then(if (look.dashed) Modifier.stippelRand(look.border, width = look.width, radius = 8.dp) else Modifier.border(BorderStroke(look.width, look.border), shape))
            .padding(horizontal = 6.dp, vertical = 12.dp),
        contentAlignment = Alignment.Center,
    ) {
        // The icon above the label, so "Verwarmen" never breaks inside the word on a 360 dp phone.
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(2.dp)) {
            if (icoon != null) PeilIcoon(icoon, look.fg, size = 18.dp)
            Text(
                label,
                color = look.fg,
                textAlign = TextAlign.Center,
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium),
            )
        }
    }
}

@Composable
private fun DetailsCard(zone: Zone, showFan: Boolean, showMode: Boolean) {
    HuisCard {
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
    HuisCard {
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
                val active = WeekSchedule.active(schedule.blocks, LocalDateTime.now(LocalHuisKlok.current.withZone(zoneId)))
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
    val texts = textSource()
    val days = WeekSchedule.dayParts(group.days)
    val spokenDays = CoreTexts.days(texts, days, spoken = true)
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            CoreTexts.days(texts, days),
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
                    // Times line up in a column (Peil "cijfers": tabular numbers).
                    style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold, fontFeatureSettings = "tnum"),
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
                if (isActive) Pill(stringResource(R.string.schedule_now), Huis.peil.infoBg, Huis.peil.infoInk, border = Huis.peil.info)
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
        HorizontalDivider(color = Huis.peil.border)
    }
}
