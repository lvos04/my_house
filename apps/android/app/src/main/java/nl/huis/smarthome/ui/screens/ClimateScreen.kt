package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.InfoBox
import nl.huis.smarthome.ui.components.OfflineBanner
import nl.huis.smarthome.ui.theme.Huis

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
            Text(zone?.name ?: stringResource(R.string.nav_climate), style = MaterialTheme.typography.headlineSmall)
        }
        OfflineBanner(state, vm::retry)

        if (zones.size > 1 && zone != null) {
            ScrollableTabRow(
                selectedTabIndex = zones.indexOf(zone),
                containerColor = Huis.colors.ground,
                edgePadding = 0.dp,
            ) {
                zones.forEach { z ->
                    Tab(selected = z.id == zone.id, onClick = { onSelectZone(z.id) }, text = { Text(z.name) })
                }
            }
        }

        if (zone == null) {
            Text(stringResource(R.string.no_zones), color = Huis.colors.muted)
            return@Column
        }

        val enabled = state.controlsEnabled && zone.online
        val draft = state.drafts[zone.id]
        val dim = if (state.online) 1f else 0.55f

        Dial(zone, draft, Modifier.align(Alignment.CenterHorizontally).alpha(dim))
        Box(Modifier.align(Alignment.CenterHorizontally)) {
            if (zone.mode != "off") {
                SetpointControl(draft ?: zone.targetC, enabled, { vm.nudge(zone, it) }, zone.capabilities.stepC, big = true)
            }
        }
        if (zone.id in state.pending || draft != null) {
            Text(
                stringResource(R.string.sent_pending),
                color = Huis.colors.muted,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.align(Alignment.CenterHorizontally),
            )
        }

        ModeSelector(zone, enabled) { vm.setMode(zone, it) }

        zone.blockedModes.forEach { b ->
            InfoBox(stringResource(R.string.mode_unavailable, NlFormat.mode(b.mode), b.reason))
        }
        zone.notice?.let { InfoBox(it, warn = true) }

        HuisCard(radius = 20) {
            InfoRow(stringResource(R.string.control), NlFormat.control(zone.control))
            zone.profile?.let { InfoRow(stringResource(R.string.profile), it) }
            zone.nextChange?.let { n ->
                InfoRow(
                    stringResource(R.string.next_change),
                    listOfNotNull(NlFormat.clock(n.at), n.targetC?.let { NlFormat.temperature(it) }).joinToString(" · "),
                )
            }
            zone.fan?.let { InfoRow(stringResource(R.string.fan), fanLabel(it)) }
            zone.humidity.value?.let { InfoRow(stringResource(R.string.humidity), NlFormat.percent(it)) }
        }

        if (zone.override != null || zone.control == "manual") {
            OutlinedButton(
                onClick = { vm.resume(zone) },
                enabled = enabled,
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                shape = RoundedCornerShape(16.dp),
                border = BorderStroke(1.dp, Huis.colors.outline),
            ) { Text(stringResource(R.string.resume_schedule), color = Huis.colors.ink) }
        } else {
            Button(
                onClick = { vm.override2h(zone) },
                enabled = enabled,
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                shape = RoundedCornerShape(16.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Huis.colors.ink, contentColor = Huis.colors.ground),
            ) { Text(stringResource(R.string.override_2h)) }
        }
    }
}

@Composable
private fun Dial(zone: Zone, draft: Double?, modifier: Modifier = Modifier) {
    val ring = when (zone.mode) {
        "heat" -> Huis.colors.heatRing
        "cool" -> Huis.colors.cool.copy(alpha = 0.35f)
        else -> Huis.colors.outline
    }
    val accent = when (zone.mode) {
        "heat" -> Huis.colors.heat
        "cool" -> Huis.colors.cool
        else -> Huis.colors.muted
    }
    val caps = zone.capabilities
    val target = draft ?: zone.targetC
    val fraction = target?.let { ((it - caps.minC) / (caps.maxC - caps.minC)).toFloat().coerceIn(0f, 1f) }
    val surface = Huis.colors.surface
    Box(modifier.size(240.dp), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(240.dp)) {
            val stroke = 12.dp.toPx()
            val inset = stroke / 2
            val arcSize = Size(size.width - stroke, size.height - stroke)
            drawCircle(surface, radius = size.minDimension / 2 - stroke)
            drawArc(ring, startAngle = 135f, sweepAngle = 270f, useCenter = false, topLeft = Offset(inset, inset), size = arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
            if (fraction != null && zone.mode != "off") {
                drawArc(accent, startAngle = 135f, sweepAngle = 270f * fraction, useCenter = false, topLeft = Offset(inset, inset), size = arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
            }
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(stringResource(R.string.target), color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium)
            Text(
                if (zone.mode == "off") NlFormat.mode("off") else NlFormat.temperature(target),
                style = MaterialTheme.typography.displayLarge,
            )
            Text(
                stringResource(R.string.now_value, NlFormat.temperature(zone.temperature.value), NlFormat.quality(zone.temperature.quality)),
                color = if (zone.temperature.stale) Huis.colors.warn else Huis.colors.muted,
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

@Composable
private fun ModeSelector(zone: Zone, enabled: Boolean, onPick: (String) -> Unit) {
    val modes = zone.capabilities.modes.ifEmpty { listOf("off", "heat", "cool") }
    Row(
        Modifier.fillMaxWidth().selectableGroup(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        modes.forEach { m ->
            val selected = zone.mode == m
            val blocked = zone.blockReason(m) != null
            val (bg, fg) = when {
                blocked -> Huis.colors.surfaceMuted to Huis.colors.muted
                selected && m == "heat" -> Huis.colors.heatContainer to Huis.colors.heat
                selected && m == "cool" -> Huis.colors.coolContainer to Huis.colors.cool
                else -> Huis.colors.surface to Huis.colors.ink
            }
            val border = when {
                selected && m == "heat" -> BorderStroke(2.dp, Huis.colors.heat)
                selected && m == "cool" -> BorderStroke(2.dp, Huis.colors.cool)
                selected -> BorderStroke(2.dp, Huis.colors.ink)
                else -> BorderStroke(1.dp, Huis.colors.outline)
            }
            val blockedLabel = stringResource(R.string.mode_unavailable, NlFormat.mode(m), zone.blockReason(m) ?: "")
            Surface(
                modifier = Modifier
                    .weight(1f)
                    .heightIn(min = 52.dp)
                    .selectable(
                        selected = selected,
                        enabled = enabled && !blocked,
                        role = Role.RadioButton,
                        onClick = { if (!selected) onPick(m) },
                    )
                    .semantics { if (blocked) stateDescription = blockedLabel },
                shape = RoundedCornerShape(16.dp),
                color = bg,
                border = border,
            ) {
                Box(contentAlignment = Alignment.Center, modifier = Modifier.padding(vertical = 14.dp)) {
                    Text(
                        NlFormat.mode(m),
                        color = fg,
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium),
                    )
                }
            }
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Column {
        Row(Modifier.fillMaxWidth().heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
            Text(value, style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold))
        }
        HorizontalDivider(color = Huis.colors.outline.copy(alpha = 0.6f))
    }
}

private fun fanLabel(fan: String): String = when (fan) {
    "auto" -> "Auto"
    "low" -> "Laag"
    "medium" -> "Middel"
    "high" -> "Hoog"
    else -> fan
}
