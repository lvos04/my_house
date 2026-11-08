package nl.huis.smarthome.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.action.actionStartActivity
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
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import nl.huis.smarthome.MainActivity
import nl.huis.smarthome.R
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.data.CachedOverview
import nl.huis.smarthome.data.OverviewCache

private val Bg = ColorProvider(day = Color(0xFFF5F4F0), night = Color(0xFF1F1E1B))
private val Ink = ColorProvider(day = Color(0xFF1C1B19), night = Color(0xFFF2F0EA))
private val Muted = ColorProvider(day = Color(0xFF5F5C55), night = Color(0xFFB8B4AA))
private val Warn = ColorProvider(day = Color(0xFF9A5B00), night = Color(0xFFE7B65C))

/**
 * Home-screen widget: the first two zone temperatures and the current power.
 * Reads only the on-disk cache written by the app, so it never needs network or the token.
 */
class HomeWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val cached = OverviewCache.read(context)
        val empty = context.getString(R.string.widget_empty)
        provideContent { WidgetContent(cached, empty) }
    }
}

class HomeWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = HomeWidget()
}

@Composable
private fun WidgetContent(cached: CachedOverview?, emptyText: String) {
    Column(
        GlanceModifier
            .fillMaxSize()
            .background(Bg)
            .cornerRadius(24.dp)
            .padding(14.dp)
            .clickable(actionStartActivity<MainActivity>()),
    ) {
        if (cached == null) {
            Text(emptyText, style = TextStyle(color = Muted, fontSize = 13.sp))
            return@Column
        }
        val o = cached.overview
        o.zones.take(2).forEach { z ->
            Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    z.name,
                    style = TextStyle(color = Muted, fontSize = 13.sp),
                    modifier = GlanceModifier.defaultWeight(),
                )
                Text(
                    NlFormat.temperature(z.temperature.value),
                    style = TextStyle(
                        color = if (z.temperature.stale) Warn else Ink,
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
                Text("Nu", style = TextStyle(color = Muted, fontSize = 13.sp), modifier = GlanceModifier.defaultWeight())
                Text(NlFormat.watts(p.totalW), style = TextStyle(color = Ink, fontSize = 16.sp, fontWeight = FontWeight.Medium))
            }
        }
        Spacer(GlanceModifier.height(4.dp))
        Text(NlFormat.relative(cached.savedAt), style = TextStyle(color = Muted, fontSize = 11.sp))
    }
}
