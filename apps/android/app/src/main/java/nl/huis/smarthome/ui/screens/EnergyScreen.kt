package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.currentStateAsState
import kotlinx.coroutines.delay
import nl.huis.smarthome.R
import nl.huis.smarthome.core.EnergySummary
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.components.ConnectionBanner
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.InfoBox
import nl.huis.smarthome.ui.components.QualityLabel
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.noFontScale
import java.text.NumberFormat
import java.time.Instant
import java.time.ZoneId
import java.util.Locale

private val consumptionRoles = listOf("airco", "boiler", "rest")

/** While the tab is visible the summary is fetched again this often; live power comes by WebSocket. */
private const val ENERGY_REFRESH_MS = 60_000L

@Composable
fun EnergyScreen(state: HomeState, vm: MainViewModel) {
    // Refresh only while this tab is on screen and the app is in the foreground: leaving the tab
    // cancels the effect, and the lifecycle state turns false when the app goes to the background.
    val lifecycleOwner = LocalLifecycleOwner.current
    val lifecycleState by lifecycleOwner.lifecycle.currentStateAsState()
    val visible = lifecycleState.isAtLeast(Lifecycle.State.STARTED)
    LaunchedEffect(visible, state.online) {
        if (!visible || !state.online) return@LaunchedEffect
        while (true) {
            vm.refreshEnergy("day")
            delay(ENERGY_REFRESH_MS)
        }
    }
    val summary = state.energy ?: state.overview?.energyToday
    val dim = if (state.online) 1f else 0.55f
    val zone = summary?.timezone?.let { runCatching { ZoneId.of(it) }.getOrNull() } ?: ZoneId.of("Europe/Amsterdam")

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                stringResource(R.string.energy_today_until, NlFormat.clock((state.updatedAt ?: Instant.now()).toString(), zone)),
                color = Huis.colors.muted,
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                stringResource(R.string.nav_energy),
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.semantics { heading() },
            )
        }
        ConnectionBanner(state, vm::retry)

        // Live power (energy.live over the WebSocket, at most every 5 s).
        state.overview?.powerNow?.let { power ->
            HuisCard(Modifier.alpha(dim)) { PowerSummary(power) }
        }

        if (summary == null) {
            Text(stringResource(R.string.no_energy), color = Huis.colors.muted)
            return@Column
        }

        HuisCard(Modifier.alpha(dim)) {
            Text(
                stringResource(R.string.energy_today_title),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.semantics { heading() },
            )
            Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(NlFormat.kwh(summary.totalKwh), style = MaterialTheme.typography.displayMedium.noFontScale())
                summary.cost?.let {
                    Text(
                        NlFormat.euro(it),
                        color = Huis.colors.muted,
                        style = MaterialTheme.typography.bodyLarge,
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                }
            }
            summary.costNote?.takeIf { it.isNotBlank() }?.let {
                Text(it, color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
            }
            if (summary.buckets.isNotEmpty()) {
                HourBars(summary, zone)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    listOf("00", "06", "12", "18", "24").forEach {
                        Text(it, color = Huis.colors.muted, style = MaterialTheme.typography.labelMedium)
                    }
                }
                Legend(summary)
                if (summary.buckets.any { !it.complete }) {
                    Text(stringResource(R.string.energy_incomplete), color = Huis.colors.warn, style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        val meters = summary.meters.filter { it.role != "grid_export" || (it.kwh ?: 0.0) > 0 }
        if (meters.isNotEmpty() || summary.gas != null) {
            HuisCard(Modifier.alpha(dim), radius = 20) {
                meters.forEachIndexed { i, m ->
                    MeterRow(
                        label = m.label.ifBlank { m.role },
                        value = NlFormat.kwh(m.kwh),
                        cost = m.cost,
                        color = roleColor(m.role),
                        quality = { QualityLabel(m.quality, m.note ?: m.notes.firstOrNull()) },
                    )
                    if (i < meters.lastIndex || summary.gas != null) HorizontalDivider(color = Huis.colors.outline)
                }
                summary.gas?.let { gas ->
                    MeterRow(
                        label = stringResource(R.string.gas),
                        value = gas.m3?.let { stringResource(R.string.gas_m3, cubicMeters(it)) } ?: "–",
                        cost = gas.cost,
                        color = null,
                        quality = { QualityLabel(gas.quality, gas.note) },
                    )
                }
            }
        }

        InfoBox(stringResource(R.string.energy_per_unit_note))
        if (summary.meters.any { it.role == "rest" }) {
            InfoBox(stringResource(R.string.energy_rest_note))
        }
    }
}

@Composable
private fun MeterRow(label: String, value: String, cost: Double?, color: Color?, quality: @Composable () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (color != null) Box(Modifier.size(10.dp).clip(RoundedCornerShape(3.dp)).background(color))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(label, style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold))
            quality()
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(value, style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold))
            cost?.let { Text(NlFormat.euro(it), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall) }
        }
    }
}

/** Colour key for the bars: airco, boiler, overig. */
@Composable
private fun Legend(summary: EnergySummary) {
    val labels = summary.meters.associate { it.role to it.label }
    // One line normally; one item per line at large font settings, so nothing is cut off.
    val stacked = LocalDensity.current.fontScale > 1.3f
    val items: @Composable () -> Unit = {
        consumptionRoles.forEach { role ->
            val fallback = stringResource(
                when (role) {
                    "airco" -> R.string.legend_airco
                    "boiler" -> R.string.legend_boiler
                    else -> R.string.legend_rest
                },
            )
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Box(Modifier.size(10.dp).clip(RoundedCornerShape(3.dp)).background(roleColor(role)))
                Text(labels[role]?.ifBlank { null } ?: fallback, color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
    if (stacked) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) { items() }
    } else {
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalAlignment = Alignment.CenterVertically) { items() }
    }
}

private fun cubicMeters(v: Double): String =
    NumberFormat.getNumberInstance(Locale.forLanguageTag("nl-NL")).apply {
        minimumFractionDigits = 2
        maximumFractionDigits = 2
    }.format(v)

/** Stacked bars per hour. Incomplete hours get a dashed outline. Described for TalkBack. */
@Composable
private fun HourBars(summary: EnergySummary, zone: ZoneId) {
    val colors = mapOf("airco" to Huis.colors.cool, "boiler" to Huis.colors.boiler, "rest" to Huis.colors.rest)
    val warn = Huis.colors.warn
    val buckets = summary.buckets
    val totals = buckets.map { b -> consumptionRoles.sumOf { b.values[it] ?: 0.0 } }
    val max = (totals.maxOrNull() ?: 0.0).takeIf { it > 0 } ?: 1.0
    val peak = totals.indices.maxByOrNull { totals[it] }?.takeIf { totals[it] > 0 }
    val description = if (peak != null) {
        stringResource(R.string.energy_per_hour_peak, NlFormat.clock(buckets[peak].start, zone), NlFormat.kwh(totals[peak]))
    } else {
        stringResource(R.string.energy_per_hour)
    }
    Canvas(
        Modifier
            .fillMaxWidth()
            .height(96.dp)
            .semantics { contentDescription = description },
    ) {
        val slots = maxOf(buckets.size, 24)
        val slot = size.width / slots
        val barW = slot * 0.72f
        val gap = 1.5.dp.toPx()
        buckets.forEachIndexed { i, b ->
            val x = i * slot + (slot - barW) / 2
            var y = size.height
            if (!b.complete) {
                drawRoundRect(
                    color = warn,
                    topLeft = Offset(x, size.height * 0.55f),
                    size = Size(barW, size.height * 0.45f),
                    cornerRadius = CornerRadius(3.dp.toPx()),
                    style = Stroke(1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 4f))),
                )
                return@forEachIndexed
            }
            consumptionRoles.forEach { role ->
                val v = b.values[role] ?: 0.0
                if (v <= 0) return@forEach
                val h = (v / max * size.height).toFloat()
                drawRoundRect(
                    color = colors[role] ?: Color.Gray,
                    topLeft = Offset(x, y - h),
                    size = Size(barW, (h - gap).coerceAtLeast(1f)),
                    cornerRadius = CornerRadius(2.dp.toPx()),
                )
                y -= h
            }
        }
    }
}
