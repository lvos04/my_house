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
import nl.huis.smarthome.ui.components.arceer
import nl.huis.smarthome.ui.components.arcering
import nl.huis.smarthome.ui.components.lastKnownClock
import nl.huis.smarthome.ui.components.stippelRand
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.LocalHuisKlok
import java.text.NumberFormat
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
    // Offline: no dimming (Peil); the cards say "Verouderd · Laatst bekend · 15:47".
    val stale = if (state.online) null else lastKnownClock(state)
    val zone = summary?.timezone?.let { runCatching { ZoneId.of(it) }.getOrNull() } ?: ZoneId.of("Europe/Amsterdam")

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                stringResource(R.string.energy_today_until, NlFormat.clock((state.updatedAt ?: LocalHuisKlok.current.instant()).toString(), zone)),
                color = Huis.colors.muted,
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                stringResource(R.string.nav_energy),
                style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.semantics { heading() },
            )
        }
        ConnectionBanner(state, vm::retry)

        // Live power (energy.live over the WebSocket, at most every 5 s).
        state.overview?.powerNow?.let { power ->
            HuisCard(verouderd = stale) { PowerSummary(power, stale = stale != null) }
        }

        if (summary == null) {
            Text(stringResource(R.string.no_energy), color = Huis.colors.muted)
            return@Column
        }

        HuisCard(verouderd = stale) {
            Text(
                stringResource(R.string.energy_today_title),
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.semantics { heading() },
            )
            Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    NlFormat.kwh(summary.totalKwh),
                    style = MaterialTheme.typography.displaySmall,
                    color = if (stale != null) Huis.colors.dimmed else Huis.colors.ink,
                )
                summary.cost?.let {
                    Text(
                        NlFormat.euro(it),
                        color = Huis.colors.muted,
                        style = MaterialTheme.typography.bodyLarge,
                        modifier = Modifier.padding(bottom = 4.dp),
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
                        Text(it, color = Huis.peil.chartLabel, style = MaterialTheme.typography.labelMedium.copy(fontFeatureSettings = "tnum"))
                    }
                }
                Legend(summary)
                if (summary.buckets.any { !it.complete }) {
                    // Incomplete = hatched with a dashed outline (Peil: geschat), not amber.
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        IncompleteSwatch()
                        Text(stringResource(R.string.energy_incomplete), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }

        val meters = summary.meters.filter { it.role != "grid_export" || (it.kwh ?: 0.0) > 0 }
        if (meters.isNotEmpty() || summary.gas != null) {
            HuisCard(verouderd = stale) {
                meters.forEachIndexed { i, m ->
                    MeterRow(
                        label = m.label.ifBlank { m.role },
                        value = NlFormat.kwh(m.kwh),
                        cost = m.cost,
                        role = m.role,
                        quality = { QualityLabel(m.quality, m.note ?: m.notes.firstOrNull()) },
                    )
                    if (i < meters.lastIndex || summary.gas != null) HorizontalDivider(color = Huis.peil.border)
                }
                summary.gas?.let { gas ->
                    MeterRow(
                        label = stringResource(R.string.gas),
                        value = gas.m3?.let { stringResource(R.string.gas_m3, cubicMeters(it)) } ?: "–",
                        cost = gas.cost,
                        role = null,
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
private fun MeterRow(label: String, value: String, cost: Double?, role: String?, quality: @Composable () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (role != null) RoleSwatch(role)
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
                RoleSwatch(role)
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

/** Hatched square with a dashed outline: the legend for an incomplete hour. */
@Composable
private fun IncompleteSwatch() {
    val p = Huis.peil
    Box(
        Modifier
            .size(12.dp)
            .clip(RoundedCornerShape(2.dp))
            .arcering(p.staleHatch, spacing = 3.dp)
            .stippelRand(p.stale, width = 1.dp, radius = 2.dp),
    )
}

/**
 * Stacked bars per hour (Peil chart colours: airco chart-1, boiler chart-3, overig chart-other with
 * hatching). Bars are rounded at the top only, with a 2 dp card-coloured gap between the parts.
 * Incomplete hours are hatched with a dashed outline. Described for TalkBack.
 */
@Composable
private fun HourBars(summary: EnergySummary, zone: ZoneId) {
    val p = Huis.peil
    val colors = mapOf("airco" to p.chart1, "boiler" to p.chart3, "rest" to p.chartOther)
    val stale = p.stale
    val hatch = p.staleHatch
    val card = p.card
    val grid = p.chartGrid
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
        val barW = minOf(slot * 0.72f, 24.dp.toPx())
        val gap = 2.dp.toPx()
        val radius = 2.dp.toPx()
        drawLine(grid, Offset(0f, size.height - 0.5f), Offset(size.width, size.height - 0.5f), strokeWidth = 1.dp.toPx())
        buckets.forEachIndexed { i, b ->
            val x = i * slot + (slot - barW) / 2
            var y = size.height
            if (!b.complete) {
                val top = size.height * 0.55f
                val area = Size(barW, size.height * 0.45f)
                arceer(hatch, 3.dp.toPx(), 1.dp.toPx(), Offset(x, top), area)
                drawRoundRect(
                    color = stale,
                    topLeft = Offset(x, top),
                    size = area,
                    cornerRadius = CornerRadius(radius),
                    style = Stroke(1.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx()))),
                )
                return@forEachIndexed
            }
            val parts = consumptionRoles.filter { (b.values[it] ?: 0.0) > 0 }
            parts.forEachIndexed { j, role ->
                val v = b.values[role] ?: 0.0
                val h = (v / max * size.height).toFloat()
                val top = j == parts.lastIndex
                val partH = (h - if (top) 0f else gap).coerceAtLeast(1f)
                val topLeft = Offset(x, y - h)
                // Rounded only at the top of the stack.
                drawRoundRect(colors[role] ?: Color.Gray, topLeft, Size(barW, partH), CornerRadius(if (top) radius else 0f))
                if (top && partH > radius) drawRect(colors[role] ?: Color.Gray, Offset(x, y - h + radius), Size(barW, partH - radius))
                if (role == "rest") arceer(card, 3.dp.toPx(), 1.dp.toPx(), topLeft, Size(barW, partH))
                y -= h
            }
        }
    }
}
