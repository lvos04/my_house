package nl.huis.smarthome.ui.theme

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WCAG contrast of the colour pairs the screens actually draw (docs/ontwerp/PEIL_MIGRATIE.md §2.3,
 * §11), in light and dark: text ≥ 4.5:1 (muted text ≥ 7:1), icons, borders of controls and chart
 * marks ≥ 3:1. Decorative lines (card border, dial track, gap) are not in the list on purpose.
 */
class PeilContrastTest {
    private fun ratio(a: Color, b: Color): Double {
        val la = a.luminance() + 0.05
        val lb = b.luminance() + 0.05
        return (maxOf(la, lb) / minOf(la, lb)).toDouble()
    }

    private fun check(mode: String, pairs: List<Triple<String, Color, Color>>, min: Double) {
        val te = pairs.filter { ratio(it.second, it.third) < min - 0.005 }
        assertTrue(
            "$mode: onder ${"%.1f".format(min)}:1 → " + te.joinToString { "${it.first} ${"%.2f".format(ratio(it.second, it.third))}" },
            te.isEmpty(),
        )
    }

    private fun text(p: PeilKleuren) = listOf(
        Triple("foreground/card", p.foreground, p.card),
        Triple("foreground/background", p.foreground, p.background),
        Triple("foreground/accent (geselecteerd)", p.foreground, p.accent),
        Triple("foreground/muted", p.foreground, p.muted),
        Triple("healthy-ink/card", p.healthyInk, p.card),
        Triple("degraded-ink/degraded-bg", p.degradedInk, p.degradedBg),
        Triple("down-ink/down-bg", p.downInk, p.downBg),
        Triple("unknown-ink/unknown-bg", p.unknownInk, p.unknownBg),
        Triple("unknown-ink/stale-bg", p.unknownInk, p.staleBg),
        Triple("info-ink/info-bg", p.infoInk, p.infoBg),
        Triple("gate-ink/gate-bg", p.gateInk, p.gateBg),
        Triple("domain-heat-ink/domain-heat-bg", p.domainHeatInk, p.domainHeatBg),
        Triple("domain-cool-ink/domain-cool-bg", p.domainCoolInk, p.domainCoolBg),
        Triple("domain-light-on-ink/domain-light-on-bg", p.domainLightOnInk, p.domainLightOnBg),
        Triple("primary-foreground/primary", p.primaryForeground, p.primary),
        Triple("destructive-foreground/destructive", p.destructiveForeground, p.destructive),
        Triple("destructive/card (tekstknop)", p.destructive, p.card),
        Triple("disabled-foreground/muted", p.disabledForeground, p.muted),
        Triple("stale-ink/card", p.staleInk, p.card),
        Triple("stale-ink/background", p.staleInk, p.background),
        Triple("chart-label/card", p.chartLabel, p.card),
    )

    private fun mutedText(p: PeilKleuren) = listOf(
        Triple("muted-foreground/card", p.mutedForeground, p.card),
        Triple("muted-foreground/background", p.mutedForeground, p.background),
    )

    private fun nonText(p: PeilKleuren) = listOf(
        Triple("border-strong/card (knop, veld, lichttegel uit)", p.borderStrong, p.card),
        Triple("border-strong/background", p.borderStrong, p.background),
        Triple("input/card", p.input, p.card),
        Triple("degraded/degraded-bg", p.degraded, p.degradedBg),
        Triple("down/down-bg", p.down, p.downBg),
        Triple("unknown/unknown-bg (stippelrand)", p.unknown, p.unknownBg),
        Triple("stale/card (stippelrand verouderd)", p.stale, p.card),
        Triple("info/info-bg", p.info, p.infoBg),
        Triple("gate/gate-bg", p.gate, p.gateBg),
        Triple("healthy/card (icoon)", p.healthy, p.card),
        Triple("primary/background (tab-onderstreping)", p.primary, p.background),
        Triple("domain-heat-ink/domain-heat-bg (rand gekozen modus)", p.domainHeatInk, p.domainHeatBg),
        Triple("domain-cool-ink/domain-cool-bg (rand gekozen modus)", p.domainCoolInk, p.domainCoolBg),
        Triple("domain-light-on-ink/domain-light-on-bg (rand lamp aan)", p.domainLightOnInk, p.domainLightOnBg),
        Triple("domain-heat/card (draaiknop)", p.domainHeat, p.card),
        Triple("domain-cool/card (draaiknop)", p.domainCool, p.card),
        Triple("chart-1/card (airco)", p.chart1, p.card),
        Triple("chart-3/card (boiler)", p.chart3, p.card),
        Triple("chart-other/card (overig)", p.chartOther, p.card),
        Triple("chart-5/card (teruglevering)", p.chart5, p.card),
    )

    @Test
    fun textPairsLight() = check("licht", text(PeilTokens.Licht), 4.5)

    @Test
    fun textPairsDark() = check("donker", text(PeilTokens.Donker), 4.5)

    @Test
    fun mutedTextIsAtLeast7() {
        check("licht", mutedText(PeilTokens.Licht), 7.0)
        check("donker", mutedText(PeilTokens.Donker), 7.0)
    }

    @Test
    fun iconsBordersAndChartMarksLight() = check("licht", nonText(PeilTokens.Licht), 3.0)

    @Test
    fun iconsBordersAndChartMarksDark() = check("donker", nonText(PeilTokens.Donker), 3.0)
}
