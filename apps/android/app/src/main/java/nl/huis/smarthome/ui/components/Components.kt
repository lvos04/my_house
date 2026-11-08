package nl.huis.smarthome.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.Quality
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.theme.Huis

@Composable
fun HuisCard(
    modifier: Modifier = Modifier,
    radius: Int = 24,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(radius.dp),
        color = Huis.colors.surface,
        shadowElevation = 1.dp,
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}

@Composable
fun Pill(text: String, container: Color, content: Color, modifier: Modifier = Modifier) {
    Text(
        text = text,
        modifier = modifier
            .clip(RoundedCornerShape(999.dp))
            .background(container)
            .padding(horizontal = 10.dp, vertical = 5.dp),
        color = content,
        style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold),
    )
}

@Composable
fun qualityColor(q: Quality): Color = when (q) {
    Quality.MEASURED -> Huis.colors.ok
    Quality.DERIVED, Quality.ESTIMATED -> Huis.colors.muted
    Quality.MISSING -> Huis.colors.warn
}

@Composable
fun QualityLabel(q: Quality, extra: String? = null) {
    Text(
        text = listOfNotNull(NlFormat.quality(q), extra).joinToString(" · "),
        color = qualityColor(q),
        style = MaterialTheme.typography.bodySmall,
    )
}

@Composable
fun StatusDot(color: Color, size: Int = 8) {
    Box(Modifier.size(size.dp).clip(CircleShape).background(color))
}

@Composable
fun InfoBox(text: String, warn: Boolean = false, modifier: Modifier = Modifier) {
    val bg = if (warn) Huis.colors.warnContainer else Huis.colors.coolContainer
    val fg = if (warn) Huis.colors.onWarnContainer else Huis.colors.onCoolContainer
    Row(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(bg)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(Icons.Outlined.Info, contentDescription = null, tint = fg, modifier = Modifier.size(18.dp))
        Text(text, color = fg, style = MaterialTheme.typography.bodyMedium)
    }
}

/** "Thuis" / "Onderweg" chip showing which route the app uses. */
@Composable
fun RouteChip(state: HomeState) {
    val (label, color) = when {
        !state.online || state.route == null -> stringResource(R.string.route_none) to Huis.colors.warn
        state.route?.kind == RouteKind.LAN -> stringResource(R.string.route_lan) to Huis.colors.ok
        else -> stringResource(R.string.route_remote) to Huis.colors.cool
    }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        StatusDot(color)
        Text(label, color = Huis.colors.muted, style = MaterialTheme.typography.bodySmall)
    }
}

/** Shown when the server can't be reached: values below are the last known ones. */
@Composable
fun OfflineBanner(state: HomeState, onRetry: () -> Unit) {
    if (state.online) return
    val since = NlFormat.relative(state.updatedAt?.toString())
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(Huis.colors.warnContainer)
            .padding(start = 14.dp, top = 6.dp, bottom = 6.dp, end = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Outlined.CloudOff, contentDescription = null, tint = Huis.colors.onWarnContainer, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(10.dp))
        Text(
            stringResource(R.string.offline_banner, since),
            color = Huis.colors.onWarnContainer,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = onRetry) { Text(stringResource(R.string.retry), color = Huis.colors.onWarnContainer) }
    }
}
