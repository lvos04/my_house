package nl.huis.smarthome.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/** Design tokens from the "Rustig huis" canvas, plus a dark variant. */
@Immutable
data class HuisColors(
    val ground: Color,
    val surface: Color,
    val surfaceMuted: Color,
    val ink: Color,
    val muted: Color,
    val outline: Color,
    val heat: Color,
    val heatContainer: Color,
    val heatRing: Color,
    val cool: Color,
    val coolContainer: Color,
    val onCoolContainer: Color,
    val ok: Color,
    val warn: Color,
    val warnContainer: Color,
    val onWarnContainer: Color,
    val error: Color,
    val light: Color,
    val onLight: Color,
    val boiler: Color,
    val rest: Color,
    val dimmed: Color,
)

val LightHuis = HuisColors(
    ground = Color(0xFFF5F4F0),
    surface = Color(0xFFFFFFFF),
    surfaceMuted = Color(0xFFF0EEE8),
    ink = Color(0xFF1C1B19),
    muted = Color(0xFF5F5C55),
    outline = Color(0xFFE4E2DC),
    heat = Color(0xFFB9470F),
    heatContainer = Color(0xFFFBEDE5),
    heatRing = Color(0xFFF3C9B2),
    cool = Color(0xFF1F5FBF),
    coolContainer = Color(0xFFEEF3FB),
    onCoolContainer = Color(0xFF173E78),
    ok = Color(0xFF1E7A46),
    warn = Color(0xFF9A5B00),
    warnContainer = Color(0xFFFFF8EA),
    onWarnContainer = Color(0xFF6B4300),
    error = Color(0xFFB42318),
    light = Color(0xFFFCF1D6),
    onLight = Color(0xFF6B5200),
    boiler = Color(0xFFC26A2E),
    rest = Color(0xFFCFCBC2),
    dimmed = Color(0xFF8C887F),
)

val DarkHuis = HuisColors(
    ground = Color(0xFF141412),
    surface = Color(0xFF1F1E1B),
    surfaceMuted = Color(0xFF2A2926),
    ink = Color(0xFFF2F0EA),
    muted = Color(0xFFB8B4AA),
    outline = Color(0xFF3A3833),
    heat = Color(0xFFF08A5D),
    heatContainer = Color(0xFF3D2418),
    heatRing = Color(0xFF7A3A1C),
    cool = Color(0xFF8FB2F0),
    coolContainer = Color(0xFF1B2A44),
    onCoolContainer = Color(0xFFC9D9F6),
    ok = Color(0xFF6CC793),
    warn = Color(0xFFE7B65C),
    warnContainer = Color(0xFF3A2E16),
    onWarnContainer = Color(0xFFF2D9A6),
    error = Color(0xFFF97066),
    light = Color(0xFF3A3220),
    onLight = Color(0xFFF1D58E),
    boiler = Color(0xFFE59866),
    rest = Color(0xFF5A5750),
    dimmed = Color(0xFF8C887F),
)

val LocalHuis = staticCompositionLocalOf { LightHuis }

// Space Grotesk / Inter are the design fonts; the app uses the system sans-serif
// so no font files or Google Play font provider are required.
private val Display = FontFamily.SansSerif

private val HuisTypography = Typography(
    displayLarge = TextStyle(fontFamily = Display, fontWeight = FontWeight.Medium, fontSize = 60.sp, lineHeight = 64.sp),
    displayMedium = TextStyle(fontFamily = Display, fontWeight = FontWeight.Medium, fontSize = 48.sp, lineHeight = 52.sp),
    displaySmall = TextStyle(fontFamily = Display, fontWeight = FontWeight.Medium, fontSize = 32.sp, lineHeight = 36.sp),
    headlineMedium = TextStyle(fontFamily = Display, fontWeight = FontWeight.SemiBold, fontSize = 28.sp, lineHeight = 34.sp),
    headlineSmall = TextStyle(fontFamily = Display, fontWeight = FontWeight.SemiBold, fontSize = 24.sp, lineHeight = 30.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 16.sp, lineHeight = 22.sp),
    titleSmall = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 14.sp, lineHeight = 20.sp),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 15.sp, lineHeight = 20.sp),
    labelMedium = TextStyle(fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 16.sp),
)

@Composable
fun HuisTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val c = if (dark) DarkHuis else LightHuis
    val scheme = if (dark) {
        darkColorScheme(
            primary = c.ink, onPrimary = c.ground, background = c.ground, onBackground = c.ink,
            surface = c.surface, onSurface = c.ink, surfaceVariant = c.surfaceMuted, onSurfaceVariant = c.muted,
            outline = c.outline, error = c.error, secondaryContainer = c.surfaceMuted, onSecondaryContainer = c.ink,
            surfaceContainer = c.surface,
        )
    } else {
        lightColorScheme(
            primary = c.ink, onPrimary = c.surface, background = c.ground, onBackground = c.ink,
            surface = c.surface, onSurface = c.ink, surfaceVariant = c.surfaceMuted, onSurfaceVariant = c.muted,
            outline = c.outline, error = c.error, secondaryContainer = Color(0xFFEAE8E2), onSecondaryContainer = c.ink,
            surfaceContainer = c.surface,
        )
    }
    CompositionLocalProvider(LocalHuis provides c) {
        MaterialTheme(colorScheme = scheme, typography = HuisTypography, content = content)
    }
}

object Huis {
    val colors: HuisColors @Composable get() = LocalHuis.current
}
