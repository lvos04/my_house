package nl.huis.smarthome.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.NotificationsActive
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.ApiProblem
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.Quality
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.data.CoreTexts
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.ui.BannerKind
import nl.huis.smarthome.ui.CommandLine
import nl.huis.smarthome.ui.ConnectionUi
import nl.huis.smarthome.ui.NotifPrompt
import nl.huis.smarthome.ui.Tone
import nl.huis.smarthome.ui.theme.Huis

@Composable
fun HuisCard(
    modifier: Modifier = Modifier,
    radius: Int = 24,
    /** Makes the whole card a button; [onClickLabel] tells TalkBack what it does. */
    onClick: (() -> Unit)? = null,
    onClickLabel: String? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(radius.dp),
        color = Huis.colors.surface,
        shadowElevation = 1.dp,
    ) {
        // The Surface clips to its shape, so the ripple stays inside the rounded corners.
        val click = if (onClick != null) Modifier.clickable(onClickLabel = onClickLabel, onClick = onClick) else Modifier
        Column(click.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
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

/** Section title, announced as a heading by TalkBack. */
@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        style = MaterialTheme.typography.titleMedium,
        color = Huis.colors.ink,
        modifier = modifier.semantics { heading() },
    )
}

@Composable
fun qualityColor(q: Quality): Color = when (q) {
    Quality.MEASURED -> Huis.colors.ok
    Quality.DERIVED, Quality.ESTIMATED -> Huis.colors.muted
    Quality.MISSING -> Huis.colors.warn
}

@Composable
fun qualityText(q: Quality): String = stringResource(
    when (q) {
        Quality.MEASURED -> R.string.quality_measured
        Quality.DERIVED -> R.string.quality_derived
        Quality.ESTIMATED -> R.string.quality_estimated
        Quality.MISSING -> R.string.quality_missing
    },
)

@Composable
fun QualityLabel(q: Quality, extra: String? = null, modifier: Modifier = Modifier) {
    Text(
        text = listOfNotNull(qualityText(q), extra?.takeIf { it.isNotBlank() }).joinToString(" · "),
        color = qualityColor(q),
        style = MaterialTheme.typography.bodySmall,
        modifier = modifier,
    )
}

@Composable
fun StatusDot(color: Color, size: Int = 8) {
    Box(Modifier.size(size.dp).clip(CircleShape).background(color))
}

/** Info (neutral blue) or warning (amber) note. Never red: nothing here is broken by the user. */
@Composable
fun InfoBox(text: String, warn: Boolean = false, modifier: Modifier = Modifier) {
    val bg = if (warn) Huis.colors.warnContainer else Huis.colors.coolContainer
    val fg = if (warn) Huis.colors.onWarnContainer else Huis.colors.onCoolContainer
    Note(text, bg, fg, if (warn) Icons.Outlined.ErrorOutline else Icons.Outlined.Info, modifier)
}

/** Calm grey note, e.g. "nog geen airco gekoppeld" or "alleen meekijken". */
@Composable
fun NeutralNote(text: String, modifier: Modifier = Modifier, icon: ImageVector = Icons.Outlined.Info) {
    Note(text, Huis.colors.surfaceMuted, Huis.colors.ink, icon, modifier)
}

@Composable
private fun Note(text: String, bg: Color, fg: Color, icon: ImageVector, modifier: Modifier) {
    Row(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(bg)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(icon, contentDescription = null, tint = fg, modifier = Modifier.padding(top = 1.dp).size(18.dp))
        Text(text, color = fg, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
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

/**
 * Connection banner (verbeterlijst 17, 37): offline (also after a 5xx or a Cloudflare page, with
 * the specific reason), server starting, or "Dit is niet jouw server". Values below it are the
 * last known ones and every control is disabled.
 */
@Composable
fun ConnectionBanner(state: HomeState, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    val banner = ConnectionUi.banner(state)
    if (banner.kind == BannerKind.NONE) return
    val since = NlFormat.relative(state.updatedAt?.toString())
    val lastKnown = if (state.overview == null) null else stringResource(R.string.banner_last_known, since)
    when (banner.kind) {
        BannerKind.NONE -> Unit
        BannerKind.CONNECTING -> BannerRow(
            icon = Icons.Outlined.Sync,
            title = stringResource(R.string.banner_connecting),
            body = lastKnown,
            bg = Huis.colors.surfaceMuted,
            fg = Huis.colors.ink,
            onRetry = null,
            modifier = modifier,
        )
        BannerKind.OFFLINE -> BannerRow(
            icon = Icons.Outlined.CloudOff,
            title = when {
                banner.problem != null -> apiProblemText(banner.problem)
                state.overview == null -> stringResource(R.string.offline_no_data)
                else -> stringResource(R.string.offline_banner, since)
            },
            body = if (banner.problem != null) lastKnown else null,
            bg = Huis.colors.warnContainer,
            fg = Huis.colors.onWarnContainer,
            onRetry = onRetry,
            modifier = modifier,
        )
        BannerKind.STARTING -> BannerRow(
            icon = Icons.Outlined.Sync,
            title = stringResource(R.string.server_starting),
            body = lastKnown,
            bg = Huis.colors.coolContainer,
            fg = Huis.colors.onCoolContainer,
            onRetry = onRetry,
            modifier = modifier,
        )
        BannerKind.WRONG_SERVER -> BannerRow(
            icon = Icons.Outlined.ErrorOutline,
            title = stringResource(R.string.wrong_server_title),
            body = listOfNotNull(stringResource(R.string.wrong_server_body), lastKnown).joinToString(" "),
            bg = Huis.colors.warnContainer,
            fg = Huis.colors.onWarnContainer,
            onRetry = onRetry,
            modifier = modifier,
        )
    }
}

/** strings.xml text for a failure the client reported as a code; [status] fills in "Serverfout (…)". */
@Composable
fun apiProblemText(problem: ApiProblem, status: Int = 0): String =
    if (problem.withStatus) stringResource(CoreTexts.of(problem), status) else stringResource(CoreTexts.of(problem))

/** Kept for older call sites. */
@Composable
fun OfflineBanner(state: HomeState, onRetry: () -> Unit) = ConnectionBanner(state, onRetry)

@Composable
private fun BannerRow(
    icon: ImageVector,
    title: String,
    body: String?,
    bg: Color,
    fg: Color,
    onRetry: (() -> Unit)?,
    modifier: Modifier,
) {
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(bg)
            // Announced when the connection state changes (e.g. "Dit is niet jouw server").
            .semantics { liveRegion = LiveRegionMode.Polite }
            .padding(start = 14.dp, top = 10.dp, bottom = if (onRetry == null) 10.dp else 2.dp, end = 8.dp),
    ) {
        Row(verticalAlignment = Alignment.Top) {
            Icon(icon, contentDescription = null, tint = fg, modifier = Modifier.padding(top = 1.dp).size(18.dp))
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, color = fg, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
                body?.let { Text(it, color = fg, style = MaterialTheme.typography.bodySmall) }
            }
        }
        if (onRetry != null) {
            TextButton(onClick = onRetry, modifier = Modifier.align(Alignment.End).heightIn(min = 48.dp)) {
                Text(stringResource(R.string.retry), color = fg)
            }
        }
    }
}

/** Shown on the main screens of a viewer phone (verbeterlijst 18). */
@Composable
fun ReadOnlyNote(state: HomeState, modifier: Modifier = Modifier) {
    if (!state.readOnly) return
    NeutralNote(
        stringResource(R.string.read_only) + " " + stringResource(R.string.read_only_detail),
        modifier,
        icon = Icons.Outlined.Visibility,
    )
}

/**
 * Status of the last command (verbeterlijst 23): "Wordt verzonden…", "Verzonden · wacht op
 * bevestiging…", "Bevestigd", "Mislukt: …", "Geweigerd: …". TalkBack reads changes aloud.
 */
@Composable
fun CommandStatusText(line: CommandLine?, modifier: Modifier = Modifier) {
    if (line == null) return
    val text = when (line) {
        CommandLine.Sending -> stringResource(R.string.command_sending)
        is CommandLine.Tracked -> {
            val label = stringResource(CoreTexts.of(line.label))
            if (line.reason == null) label else stringResource(R.string.command_with_reason, label, line.reason)
        }
        is CommandLine.Elsewhere -> stringResource(if (line.sent) R.string.command_elsewhere_sent else R.string.command_elsewhere)
    }
    val tone = (line as? CommandLine.Tracked)?.tone ?: Tone.MUTED
    val color = when (tone) {
        Tone.MUTED -> Huis.colors.muted
        Tone.OK -> Huis.colors.ok
        Tone.WARN -> Huis.colors.warn
    }
    Text(
        text,
        color = color,
        style = MaterialTheme.typography.bodySmall.copy(fontWeight = if (tone == Tone.WARN) FontWeight.SemiBold else FontWeight.Normal),
        modifier = modifier.semantics { liveRegion = LiveRegionMode.Polite },
    )
}

/**
 * Explains notifications before Android asks (never asked on start), or offers the settings
 * when alerts are waiting while notifications are off.
 */
@Composable
fun NotificationPromptCard(
    prompt: NotifPrompt,
    waiting: Int,
    onAllow: () -> Unit,
    onLater: () -> Unit,
    onSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (prompt == NotifPrompt.NONE) return
    HuisCard(modifier, radius = 20) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Top) {
            Icon(Icons.Outlined.NotificationsActive, contentDescription = null, tint = Huis.colors.heat, modifier = Modifier.size(22.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                SectionTitle(stringResource(R.string.notifications_explain_title))
                val body = if (prompt == NotifPrompt.EXPLAIN) {
                    stringResource(R.string.notifications_explain_body)
                } else {
                    LocalContext.current.resources.getQuantityString(R.plurals.notifications_waiting_count, waiting, waiting)
                }
                Text(body, color = Huis.colors.muted, style = MaterialTheme.typography.bodyMedium)
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            if (prompt == NotifPrompt.EXPLAIN) {
                Button(
                    onClick = onAllow,
                    modifier = Modifier.heightIn(min = 48.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Huis.colors.ink, contentColor = Huis.colors.ground),
                ) { Text(stringResource(R.string.notifications_allow)) }
                TextButton(onClick = onLater, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text(stringResource(R.string.not_now), color = Huis.colors.ink)
                }
            } else {
                OutlinedButton(onClick = onSettings, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text(stringResource(R.string.notifications_settings), color = Huis.colors.ink)
                }
            }
        }
    }
}

@Composable
fun modeLabel(mode: String): String = when (mode) {
    "off" -> stringResource(R.string.mode_off)
    "heat" -> stringResource(R.string.mode_heat)
    "cool" -> stringResource(R.string.mode_cool)
    "auto" -> stringResource(R.string.mode_auto)
    "dry" -> stringResource(R.string.mode_dry)
    "fan" -> stringResource(R.string.mode_fan)
    else -> mode
}

@Composable
fun fanLabel(fan: String): String = when (fan) {
    "auto" -> stringResource(R.string.fan_auto)
    "low" -> stringResource(R.string.fan_low)
    "medium" -> stringResource(R.string.fan_medium)
    "high" -> stringResource(R.string.fan_high)
    else -> fan
}

/** Container and content colour for a mode (heat orange, cool blue, else neutral). */
@Composable
fun modeColors(mode: String): Pair<Color, Color> = when (mode) {
    "heat" -> Huis.colors.heatContainer to Huis.colors.heat
    "cool" -> Huis.colors.coolContainer to Huis.colors.cool
    else -> Huis.colors.surfaceMuted to Huis.colors.muted
}
