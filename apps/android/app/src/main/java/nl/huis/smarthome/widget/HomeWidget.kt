package nl.huis.smarthome.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.ColorFilter
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.color.ColorProvider
import androidx.glance.layout.Alignment
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import nl.huis.smarthome.MainActivity
import nl.huis.smarthome.R
import nl.huis.smarthome.core.Freshness
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.data.CachedOverview
import nl.huis.smarthome.data.CoreTexts
import nl.huis.smarthome.data.OverviewCache
import nl.huis.smarthome.data.TextSource
import nl.huis.smarthome.ui.theme.PeilTokens
import nl.huis.smarthome.huisApp
import nl.huis.smarthome.work.AlertWorker
import java.time.Instant

// Peil colours (design/ontwerptaal/tokens.json via PeilTokens), light and dark: card, foreground,
// muted-foreground (≥ 7:1 on the card), stale ink for last known values. Never dimmed with alpha.
private val Bg = ColorProvider(day = PeilTokens.Licht.card, night = PeilTokens.Donker.card)
private val Ink = ColorProvider(day = PeilTokens.Licht.foreground, night = PeilTokens.Donker.foreground)
private val Muted = ColorProvider(day = PeilTokens.Licht.mutedForeground, night = PeilTokens.Donker.mutedForeground)
private val StaleInk = ColorProvider(day = PeilTokens.Licht.staleInk, night = PeilTokens.Donker.staleInk)
private val Stale = ColorProvider(day = PeilTokens.Licht.stale, night = PeilTokens.Donker.stale)

/** Texts the widget needs, read once per update from the string resources. */
private data class WidgetTexts(
    val empty: String,
    val stale: String,
    val unpaired: String,
    val powerNow: String,
    /** "Bijgewerkt 3 min geleden" / "Nog niet bijgewerkt" for the cached data. */
    val age: String,
)

/**
 * Home-screen widget: the first two zone temperatures and the current power, with the age of
 * the data. Data older than 30 minutes is marked "Verouderd" with a clock-alert icon and shown in
 * the stale ink (Peil: never dimmed). It reads only the
 * on-disk cache; the background worker ([AlertWorker]) refreshes that cache every 15 minutes,
 * also while the app is closed.
 */
class HomeWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val cached = OverviewCache.read(context)
        val unpaired = runCatching { context.huisApp.repository.state.value.unpaired != null }.getOrDefault(false)
        val now = Instant.now()
        val texts = WidgetTexts(
            empty = context.getString(R.string.widget_empty),
            stale = context.getString(R.string.widget_stale),
            unpaired = context.getString(R.string.widget_unpaired),
            powerNow = context.getString(R.string.power_now),
            age = CoreTexts.age(TextSource.of(context), Freshness.age(cached?.savedAt, now)),
        )
        provideContent { WidgetContent(cached, unpaired, texts, now) }
    }
}

class HomeWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = HomeWidget()

    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        // First widget placed: make sure the background refresh runs.
        val s = runCatching { context.huisApp.repository.state.value }.getOrNull() ?: return
        if (s.paired && s.unpaired == null) AlertWorker.schedule(context)
    }
}

@Composable
private fun WidgetContent(cached: CachedOverview?, unpaired: Boolean, texts: WidgetTexts, now: Instant) {
    Column(
        GlanceModifier
            .fillMaxSize()
            .background(Bg)
            .cornerRadius(12.dp)
            .padding(14.dp)
            .clickable(actionStartActivity<MainActivity>()),
    ) {
        if (cached == null) {
            Text(if (unpaired) texts.unpaired else texts.empty, style = TextStyle(color = Muted, fontSize = 14.sp))
            return@Column
        }
        val stale = unpaired || Freshness.isStale(cached.savedAt, now)
        val valueColor = if (stale) StaleInk else Ink
        val o = cached.overview
        o.zones.take(2).forEach { z ->
            val temp = NlFormat.temperature(z.temperature.value)
            Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    z.name,
                    style = TextStyle(color = Muted, fontSize = 14.sp),
                    modifier = GlanceModifier.defaultWeight(),
                )
                Text(
                    temp,
                    style = TextStyle(
                        color = if (!stale && z.temperature.stale) StaleInk else valueColor,
                        fontSize = 22.sp,
                        fontWeight = FontWeight.Medium,
                    ),
                )
            }
            Spacer(GlanceModifier.height(4.dp))
        }
        o.powerNow?.let { p ->
            Spacer(GlanceModifier.height(6.dp))
            Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(texts.powerNow, style = TextStyle(color = Muted, fontSize = 14.sp), modifier = GlanceModifier.defaultWeight())
                Text(NlFormat.watts(p.totalW), style = TextStyle(color = valueColor, fontSize = 16.sp, fontWeight = FontWeight.Medium))
            }
        }
        Spacer(GlanceModifier.height(4.dp))
        val footer = when {
            unpaired -> texts.unpaired
            stale -> "${texts.stale} · ${texts.age}"
            else -> texts.age
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (stale) {
                // Icon + word: "Verouderd" is never shown by colour alone.
                Image(
                    provider = ImageProvider(R.drawable.lucide_clock_alert),
                    contentDescription = null,
                    colorFilter = ColorFilter.tint(Stale),
                    modifier = GlanceModifier.size(16.dp),
                )
                Spacer(GlanceModifier.width(4.dp))
            }
            Text(footer, style = TextStyle(color = if (stale) StaleInk else Muted, fontSize = 14.sp))
        }
    }
}
