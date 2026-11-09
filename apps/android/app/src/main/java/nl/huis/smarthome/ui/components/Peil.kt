package nl.huis.smarthome.ui.components

import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import nl.huis.smarthome.R
import nl.huis.smarthome.ui.Ernst
import nl.huis.smarthome.ui.MeldingSoort
import nl.huis.smarthome.ui.PeilStatus
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.PeilKleuren

// ---------------------------------------------------------------------------------------------
// Peil building blocks for Compose (docs/ontwerp/PEIL_MIGRATIE.md §11): status as icon + word +
// shape, notes, severity labels, buttons of 48 dp, dashed borders and hatching instead of alpha.
// ---------------------------------------------------------------------------------------------

/** Touch targets on Android: 48 dp (wider than Peil's 44 px, PEIL_MIGRATIE §11.1). */
val Aanraakdoel: Dp = 48.dp

/** Peil `--radius`: cards, buttons, fields. */
val PeilVorm = RoundedCornerShape(8.dp)

/** A lucide icon (res/drawable/lucide_*.xml) in [tint]; decorative, the text next to it says what it means. */
@Composable
fun PeilIcoon(@DrawableRes id: Int, tint: Color, modifier: Modifier = Modifier, size: Dp = 20.dp) {
    Icon(painterResource(id), contentDescription = null, tint = tint, modifier = modifier.size(size))
}

/** Dashed border (Onbekend, Verouderd, disabled): drawn over the content, inside the bounds. */
fun Modifier.stippelRand(color: Color, width: Dp = 2.dp, radius: Dp = 8.dp, pill: Boolean = false): Modifier =
    drawWithContent {
        drawContent()
        val w = width.toPx()
        val r = if (pill) (size.height - w) / 2 else radius.toPx()
        drawRoundRect(
            color = color,
            topLeft = Offset(w / 2, w / 2),
            size = Size(size.width - w, size.height - w),
            cornerRadius = CornerRadius(r, r),
            style = Stroke(w, pathEffect = PathEffect.dashPathEffect(floatArrayOf(3 * w, 2 * w))),
        )
    }

/** 45° hatching behind the content (stale value field, incomplete bar); clip the shape first. */
fun Modifier.arcering(color: Color, spacing: Dp = 6.dp, width: Dp = 1.dp): Modifier = drawBehind { arceer(color, spacing.toPx(), width.toPx()) }

fun DrawScope.arceer(color: Color, spacing: Float, width: Float, topLeft: Offset = Offset.Zero, area: Size = size) {
    clipRect(topLeft.x, topLeft.y, topLeft.x + area.width, topLeft.y + area.height) {
        var x = -area.height
        while (x < area.width) {
            drawLine(
                color,
                start = Offset(topLeft.x + x, topLeft.y + area.height),
                end = Offset(topLeft.x + x + area.height, topLeft.y),
                strokeWidth = width,
            )
            x += spacing
        }
    }
}

private data class StatusKleur(val vlak: Color, val rand: Color, val inkt: Color, val icoon: Color)

private fun statusKleur(p: PeilKleuren, s: PeilStatus): StatusKleur = when (s) {
    PeilStatus.GEZOND -> StatusKleur(Color.Transparent, Color.Transparent, p.healthyInk, p.healthy)
    PeilStatus.BEPERKT -> StatusKleur(p.degradedBg, p.degraded, p.degradedInk, p.degraded)
    PeilStatus.STORING -> StatusKleur(p.downBg, p.down, p.downInk, p.down)
    PeilStatus.ONBEKEND, PeilStatus.NIET_GEKOPPELD -> StatusKleur(p.unknownBg, p.unknown, p.unknownInk, p.unknown)
    PeilStatus.VEROUDERD -> StatusKleur(p.staleBg, p.stale, p.unknownInk, p.stale)
    PeilStatus.GEPAUZEERD -> StatusKleur(p.infoBg, p.info, p.infoInk, p.info)
}

/**
 * StatusBadge (Peil C-03): icon 18 dp + fixed word in a pill with a 2 dp border (dashed for
 * Onbekend/Verouderd/Niet gekoppeld). [label] replaces the word where the screen already says
 * the same with its own text (e.g. "Niet bereikbaar"); the icon and shape still carry the status.
 */
@Composable
fun StatusBadge(status: PeilStatus, modifier: Modifier = Modifier, label: String? = null) {
    val k = statusKleur(Huis.peil, status)
    val hatch = Huis.peil.staleHatch
    val shape = RoundedCornerShape(50)
    val rand = when {
        status == PeilStatus.GEZOND -> Modifier
        status.dashed -> Modifier.stippelRand(k.rand, pill = true)
        else -> Modifier.border(BorderStroke(2.dp, k.rand), shape)
    }
    Row(
        modifier
            .heightIn(min = 32.dp)
            .clip(shape)
            .background(k.vlak)
            .then(if (status == PeilStatus.VEROUDERD) Modifier.arcering(hatch) else Modifier)
            .then(rand)
            .padding(horizontal = if (status == PeilStatus.GEZOND) 0.dp else 10.dp, vertical = 4.dp)
            .semantics(mergeDescendants = true) {},
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        PeilIcoon(status.icoon, k.icoon, size = 18.dp)
        Text(
            label ?: stringResource(status.woord),
            color = k.inkt,
            style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold),
        )
    }
}

/**
 * ErnstLabel (Peil T-20): a word with a thick bar on the left (Info · Middel · Hoog). Severity is
 * not health: no status icon, no pill.
 */
@Composable
fun ErnstLabel(ernst: Ernst, modifier: Modifier = Modifier) {
    val p = Huis.peil
    val (vlak, inkt, balk) = when (ernst) {
        Ernst.HOOG -> Triple(p.downBg, p.downInk, p.down)
        Ernst.MIDDEL -> Triple(p.muted, p.foreground, p.foreground)
        Ernst.INFO -> Triple(p.infoBg, p.infoInk, p.info)
    }
    val woord = stringResource(ernst.woord)
    val spoken = stringResource(R.string.severity_spoken, woord)
    Box(
        modifier
            .heightIn(min = 32.dp)
            .clip(RoundedCornerShape(4.dp))
            .background(vlak)
            .drawBehind { drawRect(balk, size = Size(6.dp.toPx(), size.height)) }
            .padding(start = 14.dp, end = 10.dp)
            .semantics { contentDescription = spoken },
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(woord, color = inkt, style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.SemiBold))
    }
}

/** Surface, border, text and icon colour of a note. */
data class MeldingKleur(val vlak: Color, val rand: Color, val inkt: Color, val icoon: Color, val dashed: Boolean)

@Composable
fun meldingKleur(soort: MeldingSoort): MeldingKleur {
    val p = Huis.peil
    return when (soort) {
        MeldingSoort.INFO -> MeldingKleur(p.infoBg, p.info, p.infoInk, p.info, false)
        MeldingSoort.BEPERKT -> MeldingKleur(p.degradedBg, p.degraded, p.degradedInk, p.degraded, false)
        MeldingSoort.STORING -> MeldingKleur(p.downBg, p.down, p.downInk, p.down, false)
        MeldingSoort.ONBEKEND -> MeldingKleur(p.unknownBg, p.unknown, p.unknownInk, p.unknown, true)
        MeldingSoort.GATE -> MeldingKleur(p.gateBg, p.gate, p.gateInk, p.gate, false)
        MeldingSoort.NEUTRAAL -> MeldingKleur(p.muted, p.borderStrong, p.foreground, p.foreground, false)
    }
}

/** Border of a note or panel: 2 dp, dashed for Onbekend. */
fun Modifier.meldingRand(k: MeldingKleur, radius: Dp = 8.dp): Modifier =
    if (k.dashed) stippelRand(k.rand, radius = radius) else border(BorderStroke(2.dp, k.rand), RoundedCornerShape(radius))

/** A note (Melding): icon + content on the surface of its kind, 2 dp border (dashed for Onbekend). */
@Composable
fun Melding(
    soort: MeldingSoort,
    modifier: Modifier = Modifier,
    @DrawableRes icoon: Int = soort.icoon,
    contentPadding: PaddingValues = PaddingValues(horizontal = 14.dp, vertical = 12.dp),
    content: @Composable RowScope.(MeldingKleur) -> Unit,
) {
    val k = meldingKleur(soort)
    Row(
        modifier
            .clip(PeilVorm)
            .background(k.vlak)
            .meldingRand(k)
            .padding(contentPadding),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        PeilIcoon(icoon, k.icoon, Modifier.padding(top = 2.dp), size = 20.dp)
        content(k)
    }
}

/** Kind of button (Peil Button): primary blue, secondary with a strong border, quiet, danger. */
enum class KnopSoort { PRIMAIR, SECUNDAIR, STIL, GEVAAR }

/**
 * Peil button: 8 dp corners, at least 48 dp high, label 16 sp semibold. Disabled is a dashed
 * border on the muted surface with the disabled text colour, never transparency (the reason is
 * shown next to it by the screen).
 */
@Composable
fun PeilKnop(
    tekst: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    soort: KnopSoort = KnopSoort.SECUNDAIR,
    enabled: Boolean = true,
    @DrawableRes icoon: Int? = null,
    inkt: Color? = null,
) {
    val p = Huis.peil
    val contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp)
    val vorm = Modifier.heightIn(min = Aanraakdoel)
    val uit = if (!enabled) Modifier.stippelRand(p.borderStrong, width = 1.5.dp) else Modifier
    val label: @Composable RowScope.(Color) -> Unit = { kleur ->
        if (icoon != null) {
            PeilIcoon(icoon, kleur, size = 20.dp)
            Box(Modifier.size(8.dp))
        }
        Text(tekst, style = MaterialTheme.typography.labelLarge)
    }
    when (soort) {
        KnopSoort.PRIMAIR, KnopSoort.GEVAAR -> {
            val (vlak, voor) = if (soort == KnopSoort.PRIMAIR) p.primary to p.primaryForeground else p.destructive to p.destructiveForeground
            Button(
                onClick = onClick,
                enabled = enabled,
                modifier = modifier.then(vorm).then(uit),
                shape = PeilVorm,
                contentPadding = contentPadding,
                colors = ButtonDefaults.buttonColors(
                    containerColor = vlak, contentColor = voor,
                    disabledContainerColor = p.muted, disabledContentColor = p.disabledForeground,
                ),
            ) { label(if (enabled) voor else p.disabledForeground) }
        }
        KnopSoort.SECUNDAIR -> {
            val voor = inkt ?: p.foreground
            OutlinedButton(
                onClick = onClick,
                enabled = enabled,
                modifier = modifier.then(vorm).then(uit),
                shape = PeilVorm,
                contentPadding = contentPadding,
                border = if (enabled) BorderStroke(1.dp, p.borderStrong) else null,
                colors = ButtonDefaults.outlinedButtonColors(
                    containerColor = p.card, contentColor = voor,
                    disabledContainerColor = p.muted, disabledContentColor = p.disabledForeground,
                ),
            ) { label(if (enabled) voor else p.disabledForeground) }
        }
        KnopSoort.STIL -> {
            val voor = inkt ?: p.foreground
            TextButton(
                onClick = onClick,
                enabled = enabled,
                modifier = modifier.then(vorm),
                shape = PeilVorm,
                contentPadding = contentPadding,
                colors = ButtonDefaults.textButtonColors(contentColor = voor, disabledContentColor = p.disabledForeground),
            ) { label(if (enabled) voor else p.disabledForeground) }
        }
    }
}

/** "Verouderd · Laatst bekend · 15:47" above the values of a card while the app is offline. */
@Composable
fun VerouderdRegel(sinds: String, modifier: Modifier = Modifier) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        StatusBadge(PeilStatus.VEROUDERD)
        Text(
            stringResource(R.string.stale_last_known, sinds),
            color = Huis.peil.staleInk,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}
