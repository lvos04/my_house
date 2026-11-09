package nl.huis.smarthome.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.core.ApiProblem
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.Quality
import nl.huis.smarthome.core.RelativeTime
import nl.huis.smarthome.data.CoreTexts
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.data.TextSource
import nl.huis.smarthome.ui.BannerKind
import nl.huis.smarthome.ui.CommandLine
import nl.huis.smarthome.ui.ConnectionUi
import nl.huis.smarthome.ui.MeldingSoort
import nl.huis.smarthome.ui.NotifPrompt
import nl.huis.smarthome.ui.OpdrachtTint
import nl.huis.smarthome.ui.PeilUi
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.LocalHuisKlok
import java.time.ZoneId

/**
 * Card (Peil C-05): 8 dp corners, a 1 dp decorative border instead of a shadow. With [verouderd]
 * (the time of the last known values while offline) the border is dashed and the card says
 * "Verouderd · Laatst bekend · 15:47" above its content instead of being dimmed.
 */
@Composable
fun HuisCard(
    modifier: Modifier = Modifier,
    /** Makes the whole card a button; [onClickLabel] tells TalkBack what it does. */
    onClick: (() -> Unit)? = null,
    onClickLabel: String? = null,
    verouderd: String? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    val p = Huis.peil
    Surface(
        modifier = modifier
            .fillMaxWidth()
            .then(if (verouderd != null) Modifier.stippelRand(p.stale, radius = 8.dp) else Modifier),
        shape = PeilVorm,
        color = p.card,
        border = if (verouderd != null) null else BorderStroke(1.dp, p.border),
    ) {
        // The Surface clips to its shape, so the ripple stays inside the rounded corners.
        val click = if (onClick != null) Modifier.clickable(onClickLabel = onClickLabel, onClick = onClick) else Modifier
        Column(click.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (verouderd != null) VerouderdRegel(verouderd)
            content()
        }
    }
}

/** Small rounded label (e.g. "nu" in the week schedule); not for health, use [StatusBadge] for that. */
@Composable
fun Pill(
    text: String,
    container: Color,
    content: Color,
    modifier: Modifier = Modifier,
    border: Color? = null,
    @DrawableRes icoon: Int? = null,
) {
    val shape = RoundedCornerShape(50)
    Row(
        modifier
            .heightIn(min = 32.dp)
            .clip(shape)
            .background(container)
            .then(if (border != null) Modifier.border(BorderStroke(2.dp, border), shape) else Modifier)
            .padding(horizontal = 10.dp, vertical = 4.dp)
            .semantics(mergeDescendants = true) {},
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (icoon != null) PeilIcoon(icoon, content, size = 18.dp)
        Text(text, color = content, style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold))
    }
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

/** Measurement quality is neutral (never green); only "geen meting" gets the Onbekend ink. */
@Composable
fun qualityColor(q: Quality): Color = if (PeilUi.kwaliteitOnbekend(q)) Huis.peil.unknownInk else Huis.peil.mutedForeground

@Composable
fun qualityText(q: Quality): String = stringResource(
    when (q) {
        Quality.MEASURED -> R.string.quality_measured
        Quality.DERIVED -> R.string.quality_derived
        Quality.ESTIMATED -> R.string.quality_estimated
        Quality.MISSING -> R.string.quality_missing
    },
)

/** "gemeten · 2 min geleden" (Meetkwaliteit C-07); "geen meting" with the Onbekend icon. */
@Composable
fun QualityLabel(q: Quality, extra: String? = null, modifier: Modifier = Modifier) {
    val word = qualityText(q)
    val rest = extra?.takeIf { it.isNotBlank() }
    // The quality word semibold (Peil C-07), the rest of the line (time, note) normal.
    val text = buildAnnotatedString {
        withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(word) }
        if (rest != null) {
            append(" · ")
            append(rest)
        }
    }
    Row(modifier, verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        if (PeilUi.kwaliteitOnbekend(q)) PeilIcoon(R.drawable.lucide_circle_help, Huis.peil.unknown, Modifier.padding(top = 2.dp), size = 16.dp)
        Text(text, color = qualityColor(q), style = MaterialTheme.typography.bodySmall)
    }
}

/** strings.xml texts for the formatters in [CoreTexts] (relative times, days, the system line). */
@Composable
@ReadOnlyComposable
fun textSource(): TextSource = TextSource.of(LocalContext.current.resources)

/** "zojuist", "3 min geleden", "gisteren 14:05" … for an ISO time from the server ("onbekend" without one). */
@Composable
@ReadOnlyComposable
fun relativeText(iso: String?): String =
    CoreTexts.relative(textSource(), RelativeTime.of(iso, now = LocalHuisKlok.current.instant()))

/** "15:47": the time of the last known values (offline), in the home's time zone. */
fun lastKnownClock(state: HomeState): String? {
    val at = state.updatedAt ?: return null
    val zone = state.overview?.home?.timezone?.let { runCatching { ZoneId.of(it) }.getOrNull() } ?: ZoneId.of("Europe/Amsterdam")
    return NlFormat.clock(at.toString(), zone)
}

/** Info (blue) or a limitation (amber) note. Never red: nothing here is broken by the user. */
@Composable
fun InfoBox(text: String, warn: Boolean = false, modifier: Modifier = Modifier) {
    Note(text, if (warn) MeldingSoort.BEPERKT else MeldingSoort.INFO, modifier)
}

/** Calm neutral note, e.g. "alleen meekijken". */
@Composable
fun NeutralNote(text: String, modifier: Modifier = Modifier, @DrawableRes icoon: Int = R.drawable.lucide_info) {
    Note(text, MeldingSoort.NEUTRAAL, modifier, icoon)
}

/** A note of any kind (Melding): icon + text, 2 dp border, dashed for Onbekend. */
@Composable
fun Note(text: String, soort: MeldingSoort, modifier: Modifier = Modifier, @DrawableRes icoon: Int = soort.icoon) {
    Melding(soort, modifier.fillMaxWidth(), icoon) { k ->
        Text(text, color = k.inkt, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
    }
}

/** "Thuis" / "Onderweg" with its icon (neutral: connected is not healthy); no route = Onbekend, dashed. */
@Composable
fun RouteChip(state: HomeState) {
    val route = PeilUi.route(state)
    val p = Huis.peil
    val ink = if (route.dashed) p.unknownInk else p.mutedForeground
    Row(
        Modifier
            .heightIn(min = 32.dp)
            .then(
                if (route.dashed) {
                    Modifier.clip(RoundedCornerShape(50)).background(p.unknownBg).stippelRand(p.unknown, pill = true)
                        .padding(horizontal = 10.dp)
                } else {
                    Modifier
                },
            )
            .semantics(mergeDescendants = true) {},
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        PeilIcoon(route.icoon, if (route.dashed) p.unknown else p.mutedForeground, size = 18.dp)
        Text(stringResource(route.tekst), color = ink, style = MaterialTheme.typography.bodySmall)
    }
}

/**
 * Connection banner (verbeterlijst 17, 37): offline (also after a 5xx or a Cloudflare page, with
 * the specific reason), server starting, or "Dit is niet jouw server". Values below it are the
 * last known ones and every control is disabled. Peil: offline = Onbekend (dashed, wifi-off), never
 * amber; starting = info; a different server answering = Storing (a measured mismatch).
 */
@Composable
fun ConnectionBanner(state: HomeState, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    val banner = ConnectionUi.banner(state)
    if (banner.kind == BannerKind.NONE) return
    val since = relativeText(state.updatedAt?.toString())
    val lastKnown = if (state.overview == null) null else stringResource(R.string.banner_last_known, since)
    when (banner.kind) {
        BannerKind.NONE -> Unit
        BannerKind.CONNECTING -> BannerRow(
            soort = MeldingSoort.NEUTRAAL,
            icoon = R.drawable.lucide_loader_circle,
            title = stringResource(R.string.banner_connecting),
            body = lastKnown,
            onRetry = null,
            modifier = modifier,
        )
        BannerKind.OFFLINE -> BannerRow(
            soort = MeldingSoort.ONBEKEND,
            icoon = R.drawable.lucide_wifi_off,
            title = when {
                banner.problem != null -> apiProblemText(banner.problem)
                state.overview == null -> stringResource(R.string.offline_no_data)
                else -> stringResource(R.string.offline_banner, since)
            },
            body = if (banner.problem != null) lastKnown else null,
            onRetry = onRetry,
            modifier = modifier,
        )
        BannerKind.STARTING -> BannerRow(
            soort = MeldingSoort.INFO,
            icoon = R.drawable.lucide_loader_circle,
            title = stringResource(R.string.server_starting),
            body = lastKnown,
            onRetry = onRetry,
            modifier = modifier,
        )
        BannerKind.WRONG_SERVER -> BannerRow(
            soort = MeldingSoort.STORING,
            icoon = R.drawable.lucide_octagon_x,
            title = stringResource(R.string.wrong_server_title),
            body = listOfNotNull(stringResource(R.string.wrong_server_body), lastKnown).joinToString(" "),
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
    soort: MeldingSoort,
    @DrawableRes icoon: Int,
    title: String,
    body: String?,
    onRetry: (() -> Unit)?,
    modifier: Modifier,
) {
    val k = meldingKleur(soort)
    Column(
        modifier
            .fillMaxWidth()
            .clip(PeilVorm)
            .background(k.vlak)
            .meldingRand(k)
            // Announced when the connection state changes (e.g. "Dit is niet jouw server").
            .semantics { liveRegion = LiveRegionMode.Polite }
            .padding(start = 14.dp, top = 12.dp, bottom = 12.dp, end = 14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            PeilIcoon(icoon, k.icoon, Modifier.padding(top = 2.dp), size = 20.dp)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, color = k.inkt, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
                body?.let { Text(it, color = k.inkt, style = MaterialTheme.typography.bodySmall) }
            }
        }
        if (onRetry != null) {
            PeilKnop(
                stringResource(R.string.retry),
                onClick = onRetry,
                soort = KnopSoort.SECUNDAIR,
                icoon = R.drawable.lucide_rotate_ccw,
                modifier = Modifier.align(Alignment.End),
            )
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
        icoon = R.drawable.lucide_eye,
    )
}

/**
 * Status of the last command (verbeterlijst 23): "Wordt verzonden…", "Verzonden · wacht op
 * bevestiging…", "Bevestigd", "Mislukt: …", "Geweigerd: …", always with its icon (Peil:
 * loader, check, octagon-x, triangle-alert). TalkBack reads changes aloud.
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
    val vorm = PeilUi.opdracht(line)
    val p = Huis.peil
    val (inkt, icoon) = when (vorm.tint) {
        OpdrachtTint.NEUTRAAL -> p.foreground to p.foreground
        OpdrachtTint.BEZIG -> p.mutedForeground to p.mutedForeground
        OpdrachtTint.BEPERKT -> p.degradedInk to p.degraded
        OpdrachtTint.STORING -> p.downInk to p.down
    }
    val strong = vorm.tint == OpdrachtTint.BEPERKT || vorm.tint == OpdrachtTint.STORING
    Row(
        modifier.semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Polite },
        verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        PeilIcoon(vorm.icoon, icoon, Modifier.padding(top = 1.dp), size = 18.dp)
        Text(
            text,
            color = inkt,
            style = MaterialTheme.typography.bodySmall.copy(fontWeight = if (strong) FontWeight.SemiBold else FontWeight.Normal),
        )
    }
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
    HuisCard(modifier) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Top) {
            PeilIcoon(R.drawable.lucide_bell_ring, Huis.peil.primary, Modifier.padding(top = 2.dp), size = 22.dp)
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
                PeilKnop(stringResource(R.string.notifications_allow), onAllow, soort = KnopSoort.PRIMAIR)
                PeilKnop(stringResource(R.string.not_now), onLater, soort = KnopSoort.STIL)
            } else {
                PeilKnop(stringResource(R.string.notifications_settings), onSettings, soort = KnopSoort.SECUNDAIR)
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

/** Surface and ink of a mode (domain chip): heat and cool in their domain colours, else neutral. */
@Composable
fun modeColors(mode: String): Pair<Color, Color> = when (mode) {
    "heat" -> Huis.colors.heatContainer to Huis.colors.heatInk
    "cool" -> Huis.colors.coolContainer to Huis.colors.onCoolContainer
    else -> Huis.colors.surfaceMuted to Huis.colors.ink
}
