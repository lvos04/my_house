package nl.huis.smarthome.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.Clock

/**
 * The colours the screens ask for by their old "Rustig huis" names (many call sites). Since the
 * move to the platform design language Peil (docs/ontwerp/PEIL_MIGRATIE.md §11.1) every value comes
 * from [PeilTokens] (generated from design/ontwerptaal/tokens.json); new code reads [Huis.peil].
 */
@Immutable
data class HuisColors(
    /** `background` */
    val ground: Color,
    /** `card` */
    val surface: Color,
    /** `muted` (a surface, not a text colour) */
    val surfaceMuted: Color,
    /** `foreground` */
    val ink: Color,
    /** `muted-foreground`: ≥ 7:1 on the card */
    val muted: Color,
    /** `border`: decorative only (cards, dividers) */
    val outline: Color,
    val heat: Color,
    val heatContainer: Color,
    val heatRing: Color,
    val heatInk: Color,
    val cool: Color,
    val coolContainer: Color,
    val coolRing: Color,
    val onCoolContainer: Color,
    /** `healthy` (icon): only for measured health, never for "connected" or "done". */
    val ok: Color,
    val warn: Color,
    val warnContainer: Color,
    val onWarnContainer: Color,
    /** `down` (icon and border); text on a light surface uses [errorInk]. */
    val error: Color,
    val errorInk: Color,
    val light: Color,
    val onLight: Color,
    val boiler: Color,
    /** "Overig" in the charts (`chart-other`, with hatching). */
    val rest: Color,
    /** `stale-ink`: last known values; never alpha. */
    val dimmed: Color,
    /** `border-strong`: border of a control (mode, fan, light tile, text field), ≥ 3:1 on the card. */
    val controlOutline: Color,
)

fun huisKleuren(p: PeilKleuren) = HuisColors(
    ground = p.background,
    surface = p.card,
    surfaceMuted = p.muted,
    ink = p.foreground,
    muted = p.mutedForeground,
    outline = p.border,
    heat = p.domainHeat,
    heatContainer = p.domainHeatBg,
    heatRing = p.domainHeatSoft,
    heatInk = p.domainHeatInk,
    cool = p.domainCool,
    coolContainer = p.domainCoolBg,
    coolRing = p.domainCoolSoft,
    onCoolContainer = p.domainCoolInk,
    ok = p.healthy,
    warn = p.degraded,
    warnContainer = p.degradedBg,
    onWarnContainer = p.degradedInk,
    error = p.down,
    errorInk = p.downInk,
    light = p.domainLightOnBg,
    onLight = p.domainLightOnInk,
    boiler = p.domainBoiler,
    rest = p.chartOther,
    dimmed = p.staleInk,
    controlOutline = p.borderStrong,
)

val LightHuis = huisKleuren(PeilTokens.Licht)
val DarkHuis = huisKleuren(PeilTokens.Donker)

val LocalHuis = staticCompositionLocalOf { LightHuis }
val LocalPeil = staticCompositionLocalOf { PeilTokens.Licht }

/**
 * The clock the screens read "nu" from (relative times, greeting, the block of the week schedule in
 * force). The system clock in the app; a fixed clock in the screenshot tests, so they stay the same.
 */
val LocalHuisKlok = staticCompositionLocalOf<Clock> { Clock.systemDefaultZone() }

// Peil: system font (Roboto on Android), no font files (ONTWERPTAAL O-01).
private val Sans = FontFamily.Default

private fun rol(naam: String, gewicht: FontWeight? = null): TextStyle {
    val r = PeilTokens.rollen.getValue(naam)
    return TextStyle(
        fontFamily = Sans,
        fontWeight = gewicht ?: FontWeight(r.gewicht),
        fontSize = r.grootteSp.sp,
        lineHeight = r.regelSp.sp,
    )
}

/**
 * Peil roles mapped onto Material 3 (PEIL_MIGRATIE §11.1): nothing below 14 sp; body 16, page title
 * 24 bold, card value 30, the dial 48 ("held").
 */
val HuisTypography = Typography(
    displayLarge = rol("held"),
    displayMedium = rol("held"),
    displaySmall = rol("waarde"),
    headlineMedium = rol("pagina"),
    headlineSmall = rol("pagina"),
    titleLarge = rol("sectie"),
    titleMedium = rol("titel"),
    titleSmall = rol("body", FontWeight.SemiBold),
    bodyLarge = rol("body"),
    bodyMedium = rol("body"),
    bodySmall = rol("meta"),
    labelLarge = rol("body", FontWeight.SemiBold),
    labelMedium = rol("meta", FontWeight.Medium),
    labelSmall = rol("meta", FontWeight.Medium),
)

/** Peil radii: 4 risk/code, 6, 8 card/button/field (`--radius`), 12 dialog/sheet. */
val HuisShapes = Shapes(
    extraSmall = RoundedCornerShape(4.dp),
    small = RoundedCornerShape(6.dp),
    medium = RoundedCornerShape(PeilTokens.RADIUS_DP.dp),
    large = RoundedCornerShape(12.dp),
    extraLarge = RoundedCornerShape(12.dp),
)

@Composable
fun HuisTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val p = if (dark) PeilTokens.Donker else PeilTokens.Licht
    val c = if (dark) DarkHuis else LightHuis
    // The primary colour of the other mode, for text on the inverse surface (snackbar action).
    val inversePrimary = if (dark) PeilTokens.Licht.primary else PeilTokens.Donker.primary
    // Blue primary = interaction (Peil); M3 buttons, switches and progress follow it.
    val scheme = if (dark) {
        darkColorScheme(
            primary = p.primary, onPrimary = p.primaryForeground,
            background = p.background, onBackground = p.foreground,
            surface = p.card, onSurface = p.foreground, surfaceVariant = p.muted, onSurfaceVariant = p.mutedForeground,
            surfaceContainer = p.card, surfaceContainerLow = p.card, surfaceContainerHigh = p.popover,
            surfaceContainerHighest = p.popover, surfaceContainerLowest = p.card,
            outline = p.borderStrong, outlineVariant = p.border,
            secondaryContainer = p.accent, onSecondaryContainer = p.foreground,
            error = p.destructive, onError = p.destructiveForeground,
            errorContainer = p.downBg, onErrorContainer = p.downInk,
            // No Material default (purple) anywhere: the remaining roles also come from Peil.
            primaryContainer = p.infoBg, onPrimaryContainer = p.infoInk,
            secondary = p.mutedForeground, onSecondary = p.card,
            tertiary = p.info, onTertiary = p.card, tertiaryContainer = p.infoBg, onTertiaryContainer = p.infoInk,
            inverseSurface = p.foreground, inverseOnSurface = p.background, inversePrimary = inversePrimary,
            surfaceTint = p.card, surfaceBright = p.card, surfaceDim = p.background,
        )
    } else {
        lightColorScheme(
            primary = p.primary, onPrimary = p.primaryForeground,
            background = p.background, onBackground = p.foreground,
            surface = p.card, onSurface = p.foreground, surfaceVariant = p.muted, onSurfaceVariant = p.mutedForeground,
            surfaceContainer = p.card, surfaceContainerLow = p.card, surfaceContainerHigh = p.popover,
            surfaceContainerHighest = p.popover, surfaceContainerLowest = p.card,
            outline = p.borderStrong, outlineVariant = p.border,
            secondaryContainer = p.accent, onSecondaryContainer = p.foreground,
            error = p.destructive, onError = p.destructiveForeground,
            errorContainer = p.downBg, onErrorContainer = p.downInk,
            // No Material default (purple) anywhere: the remaining roles also come from Peil.
            primaryContainer = p.infoBg, onPrimaryContainer = p.infoInk,
            secondary = p.mutedForeground, onSecondary = p.card,
            tertiary = p.info, onTertiary = p.card, tertiaryContainer = p.infoBg, onTertiaryContainer = p.infoInk,
            inverseSurface = p.foreground, inverseOnSurface = p.background, inversePrimary = inversePrimary,
            surfaceTint = p.card, surfaceBright = p.card, surfaceDim = p.background,
        )
    }
    CompositionLocalProvider(LocalHuis provides c, LocalPeil provides p) {
        MaterialTheme(colorScheme = scheme, typography = HuisTypography, shapes = HuisShapes, content = content)
    }
}

object Huis {
    val colors: HuisColors @Composable get() = LocalHuis.current

    /** All Peil tokens of the current mode (light or dark). */
    val peil: PeilKleuren @Composable get() = LocalPeil.current
}

/**
 * The same text style without the user's font scale. Only for the big number inside the dial,
 * a graphic of fixed size; everything else scales with the system font size.
 */
@Composable
fun TextStyle.noFontScale(): TextStyle {
    val density = LocalDensity.current
    return copy(
        fontSize = with(density) { fontSize.value.dp.toSp() },
        lineHeight = if (lineHeight.isSp) with(density) { lineHeight.value.dp.toSp() } else lineHeight,
    )
}
