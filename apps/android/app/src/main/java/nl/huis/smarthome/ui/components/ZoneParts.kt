package nl.huis.smarthome.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.LinkOff
import androidx.compose.material.icons.outlined.Remove
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.ui.HoursMinutes
import nl.huis.smarthome.ui.Spoken
import nl.huis.smarthome.ui.ZoneHint
import nl.huis.smarthome.ui.ZonePill
import nl.huis.smarthome.ui.ZoneUi
import nl.huis.smarthome.ui.theme.Huis

/** "Doeltemperatuur 21,5 graden" for TalkBack. */
@Composable
fun targetSpoken(value: Double?): String =
    Spoken.temperature(value)?.let { stringResource(R.string.target_spoken, it) } ?: stringResource(R.string.target_unknown_spoken)

/**
 * − value + for a zone's target. The value is a live region, so TalkBack reads the new target
 * after every tap. [showValue] is false on the Klimaat screen, where the dial shows it.
 */
@Composable
fun SetpointControl(
    value: Double?,
    enabled: Boolean,
    onNudge: (Double) -> Unit,
    step: Double,
    big: Boolean = false,
    showValue: Boolean = !big,
) {
    val btn = if (big) 64.dp else 48.dp
    val lower = stringResource(R.string.target_lower)
    val higher = stringResource(R.string.target_higher)
    val spoken = targetSpoken(value)
    val delta = if (step > 0) step else 0.5
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        FilledTonalIconButton(
            onClick = { onNudge(-delta) },
            enabled = enabled,
            modifier = Modifier.size(btn).semantics { contentDescription = lower },
            colors = IconButtonDefaults.filledTonalIconButtonColors(containerColor = Huis.colors.surfaceMuted, contentColor = Huis.colors.ink),
        ) { Icon(Icons.Outlined.Remove, contentDescription = null) }
        if (showValue) {
            Column(
                Modifier
                    .widthIn(min = 60.dp)
                    .clearAndSetSemantics {
                        contentDescription = spoken
                        liveRegion = LiveRegionMode.Polite
                    },
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(stringResource(R.string.target), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
                Text(NlFormat.temperature(value), style = MaterialTheme.typography.titleMedium)
            }
        }
        FilledTonalIconButton(
            onClick = { onNudge(delta) },
            enabled = enabled,
            modifier = Modifier.size(btn).semantics { contentDescription = higher },
            colors = IconButtonDefaults.filledTonalIconButtonColors(containerColor = Huis.colors.surfaceMuted, contentColor = Huis.colors.ink),
        ) { Icon(Icons.Outlined.Add, contentDescription = null) }
    }
}

/** Target as plain text, for viewer phones (no buttons). */
@Composable
fun TargetReadout(value: Double?, modifier: Modifier = Modifier) {
    val spoken = targetSpoken(value)
    Column(
        modifier.widthIn(min = 60.dp).clearAndSetSemantics { contentDescription = spoken },
        horizontalAlignment = Alignment.End,
    ) {
        Text(stringResource(R.string.target), color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
        Text(NlFormat.temperature(value), style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
fun ZonePillView(zone: Zone) {
    when (ZoneUi.pill(zone)) {
        ZonePill.NO_UNIT -> Pill(stringResource(R.string.zone_no_unit_pill), Huis.colors.surfaceMuted, Huis.colors.muted)
        ZonePill.UNREACHABLE -> Pill(stringResource(R.string.zone_unreachable_pill), Huis.colors.warnContainer, Huis.colors.onWarnContainer)
        ZonePill.MANUAL -> Pill(
            zone.override?.until?.let { stringResource(R.string.override_until, NlFormat.clock(it)) } ?: stringResource(R.string.zone_manual_pill),
            Huis.colors.warnContainer,
            Huis.colors.onWarnContainer,
        )
        ZonePill.MODE -> {
            val (bg, fg) = modeColors(zone.mode)
            Pill(modeLabel(zone.mode), bg, fg)
        }
    }
}

/** Why +/−, fan and "Handmatig" are off (verbeterlijst 13). Offline is a warning, "uit" is info. */
@Composable
fun ZoneHintNote(hint: ZoneHint?, modifier: Modifier = Modifier) {
    when (hint) {
        null -> Unit
        ZoneHint.CHOOSE_MODE -> InfoBox(stringResource(R.string.hint_choose_mode), modifier = modifier)
        ZoneHint.UNIT_OFFLINE -> InfoBox(stringResource(R.string.hint_unit_offline), warn = true, modifier = modifier)
    }
}

/** Neutral block for a zone without airco (verbeterlijst 31): never red, sensor values if any. */
@Composable
fun UnitNotLinked(zone: Zone, canManage: Boolean, modifier: Modifier = Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(Icons.Outlined.LinkOff, contentDescription = null, tint = Huis.colors.muted, modifier = Modifier.size(18.dp))
            Text(
                stringResource(R.string.zone_no_unit_title),
                style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold),
                color = Huis.colors.ink,
            )
        }
        Text(
            stringResource(if (ZoneUi.hasSensor(zone)) R.string.zone_no_unit_sensor else R.string.zone_no_unit_body),
            color = Huis.colors.muted,
            style = MaterialTheme.typography.bodyMedium,
        )
        Text(
            stringResource(if (canManage) R.string.zone_link_hint else R.string.zone_link_hint_other),
            color = Huis.colors.muted,
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

/** "2 uur", "90 minuten", "1 uur 30 min". */
@Composable
fun durationText(minutes: Int): String {
    val d = HoursMinutes.of(minutes)
    return when {
        d.hours == 0 -> stringResource(R.string.duration_minutes, d.minutes)
        d.minutes == 0 -> stringResource(R.string.duration_hours, d.hours)
        else -> stringResource(R.string.duration_hours_minutes, d.hours, d.minutes)
    }
}

/** How the zone is regulated now, in plain Dutch. */
@Composable
fun controlLabel(zone: Zone): String = when (zone.control) {
    "schedule" -> stringResource(R.string.control_schedule)
    "manual" -> zone.override?.until?.let { stringResource(R.string.override_until, NlFormat.clock(it)) }
        ?: stringResource(R.string.control_manual)
    "oem_fallback" -> stringResource(R.string.control_oem)
    "off" -> stringResource(R.string.control_off)
    else -> zone.control
}
