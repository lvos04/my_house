package nl.huis.smarthome.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.EnergySummary
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.components.HuisCard
import nl.huis.smarthome.ui.components.InfoBox
import nl.huis.smarthome.ui.components.OfflineBanner
import nl.huis.smarthome.ui.components.QualityLabel
import nl.huis.smarthome.ui.theme.Huis
import java.time.Instant

private val consumptionRoles = listOf("airco", "boiler", "rest")

@Composable
fun EnergyScreen(state: HomeState, vm: MainViewModel) {
    LaunchedEffect(state.online) { if (state.online) vm.loadEnergy() }
    val summary = state.energy ?: state.overview?.energyToday
    val dim = if (state.online) 1f else 0.55f

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                stringResource(R.string.energy_today_until, NlFormat.clock(state.updatedAt?.toString() ?: Instant.now().toString())),
                color = Huis.colors.muted,
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(stringResource(R.string.nav_energy), style = MaterialTheme.typography.headlineMedium)
        }
        OfflineBanner(state, vm::retry)

        if (summary == null) {
            Text(stringResource(R.string.no_energy), color = Huis.colors.muted)
            return@Column
        }

        HuisCard(Modifier.alpha(dim)) {
            Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(NlFormat.kwh(summary.totalKwh), style = MaterialTheme.typography.displayMedium)
                summary.cost?.let {
                    Text(NlFormat.euro(it), color = Huis.colors.muted, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(bottom = 8.dp))
                }
            }
            if (summary.buckets.isNotEmpty()) {
                HourBars(summary)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    listOf("00", "06", "12", "18", "24").forEach {
                        Text(it, color = Huis.colors.muted, style = MaterialTheme.typography.labelMedium)
                    }
                }
                if (summary.buckets.any { !it.complete }) {
                    Text(stringResource(R.string.energy_incomplete), color = Huis.colors.warn, style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        val meters = summary.meters.filter { it.role != "grid_export" || (it.kwh ?: 0.0) > 0 }
        if (meters.isNotEmpty()) {
            HuisCard(Modifier.alpha(dim), radius = 20) {
                meters.forEachIndexed { i, m ->
                    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(m.label, style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold))
                            QualityLabel(m.quality, m.note)
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text(NlFormat.kwh(m.kwh), style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold))
                            m.cost?.let { Text(NlFormat.euro(it), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall) }
                        }
                    }
                    if (i < meters.lastIndex) HorizontalDivider(color = Huis.colors.outline.copy(alpha = 0.6f))
                }
            }
        }

        InfoBox(stringResource(R.string.energy_per_unit_note), warn = true)
        if (summary.meters.any { it.role == "rest" }) {
            InfoBox(stringResource(R.string.energy_rest_note))
        }
    }
}

/** Stacked bars per bucket (hour for a day summary). Incomplete buckets get a dashed outline. */
@Composable
private fun HourBars(summary: EnergySummary) {
    val colors = mapOf("airco" to Huis.colors.cool, "boiler" to Huis.colors.boiler, "rest" to Huis.colors.rest)
    val warn = Huis.colors.warn
    val buckets = summary.buckets
    val totals = buckets.map { b -> consumptionRoles.sumOf { b.values[it] ?: 0.0 } }
    val max = (totals.maxOrNull() ?: 0.0).takeIf { it > 0 } ?: 1.0
    val description = stringResource(R.string.energy_per_hour)
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
