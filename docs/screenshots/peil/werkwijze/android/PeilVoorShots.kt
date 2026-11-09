package nl.huis.smarthome.peil

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material.icons.outlined.Thermostat
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.requiredHeight
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.ui.res.stringResource
import app.cash.paparazzi.DeviceConfig
import app.cash.paparazzi.Paparazzi
import com.android.resources.NightMode
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import nl.huis.smarthome.R
import nl.huis.smarthome.core.ConnectionStatus
import nl.huis.smarthome.core.EnergySummary
import nl.huis.smarthome.core.Overview
import nl.huis.smarthome.core.RealtimeState
import nl.huis.smarthome.core.Route
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.core.ShcJson
import nl.huis.smarthome.core.UnpairReason
import nl.huis.smarthome.core.ZoneSchedule
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.data.Pairing
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.NotifAction
import nl.huis.smarthome.ui.NotifPrompt
import nl.huis.smarthome.ui.NotifUi
import nl.huis.smarthome.ui.PairUi
import nl.huis.smarthome.ui.screens.ClimateScreen
import nl.huis.smarthome.ui.screens.EnergyScreen
import nl.huis.smarthome.ui.screens.HomeScreen
import nl.huis.smarthome.ui.screens.MoreScreen
import nl.huis.smarthome.ui.screens.PairScreen
import nl.huis.smarthome.ui.screens.UnpairedScreen
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.HuisTheme
import org.junit.Rule
import org.junit.Test
import org.mockito.Mockito
import java.time.Instant

/**
 * Schermafdrukken "voor" (Peil-migratie). ALLEEN in een kopie buiten de repo: de app zelf heeft geen
 * screenshot-tests. De schermen zijn de echte composables uit de app; de schil hieronder is een
 * letterlijke kopie van HuisRoot (Scaffold + NavigationBar) zodat een tab gekozen kan worden.
 * Gegevens: JSON uit de echte API van dezelfde testopstelling als de web-afdrukken (fixtures).
 */
abstract class PeilVoorShots(private val dark: Boolean, private val device: DeviceConfig, private val width: Int) {
    @get:Rule
    val paparazzi = Paparazzi(
        deviceConfig = device.copy(nightMode = if (dark) NightMode.NIGHT else NightMode.NOTNIGHT),
        maxPercentDifference = 100.0,
    )

    private val theme = if (dark) "donker" else "licht"

    private fun res(name: String): String = javaClass.getResource("/peil/$name")!!.readText()
    private val overview: Overview = ShcJson.decodeFromString(Overview.serializer(), res("overview.json"))
    private val energy: EnergySummary = ShcJson.decodeFromString(EnergySummary.serializer(), res("energy-day.json"))
    private val schedule: ZoneSchedule = ShcJson.decodeFromString(ZoneSchedule.serializer(), res("schedule-woonkamer.json"))

    private fun pairing(role: String = "control") = Pairing(
        token = "fictief", lanUrl = "http://192.168.1.20:8088", remoteUrl = null, homeId = overview.home.id,
        homeName = overview.home.name, username = "beheerder", role = "admin", serverId = "peil-test",
        clientRole = role, canControl = role != "viewer", isAdmin = role == "admin",
    )

    private fun online(role: String = "control") = HomeState(
        pairing = pairing(role), overview = overview, updatedAt = Instant.now().minusSeconds(20),
        route = Route(RouteKind.LAN, "http://192.168.1.20:8088", "peil-test"), online = true,
        connection = ConnectionStatus.ONLINE, realtime = RealtimeState.CONNECTED, energy = energy,
        schedules = overview.zones.firstOrNull { it.name == "Woonkamer" }?.let { mapOf(it.id to schedule) }.orEmpty(),
    )

    private fun offline() = online().copy(
        online = false, connection = ConnectionStatus.OFFLINE, realtime = RealtimeState.DISCONNECTED,
        updatedAt = Instant.now().minusSeconds(7 * 60),
    )

    private val vm: MainViewModel = Mockito.mock(MainViewModel::class.java).also {
        Mockito.`when`(it.pair).thenReturn(MutableStateFlow(PairUi()))
        Mockito.`when`(it.unpairResult).thenReturn(MutableStateFlow(null))
        Mockito.`when`(it.unpairing).thenReturn(MutableStateFlow(false))
        Mockito.`when`(it.messages).thenReturn(MutableSharedFlow())
        Mockito.`when`(it.cfAccess()).thenReturn(null to null)
    }

    private val notif = NotifUi(
        canPost = true, prompt = NotifPrompt.NONE, action = NotifAction.REQUEST, waiting = 0,
        onAllow = {}, onLater = {}, onSettings = {},
    )

    private enum class Tab(val label: Int, val icon: androidx.compose.ui.graphics.vector.ImageVector) {
        START(R.string.nav_start, Icons.Outlined.Home),
        CLIMATE(R.string.nav_climate, Icons.Outlined.Thermostat),
        ENERGY(R.string.nav_energy, Icons.Outlined.Bolt),
        MORE(R.string.nav_more, Icons.Outlined.MoreHoriz),
    }

    /** Letterlijk de schil van HuisRoot (Scaffold, NavigationBar, kleuren). */
    @Composable
    private fun Shell(tab: Tab, content: @Composable () -> Unit) {
        Scaffold(
            containerColor = Huis.colors.ground,
            bottomBar = {
                NavigationBar(containerColor = Huis.colors.surface) {
                    Tab.entries.forEach { t ->
                        NavigationBarItem(
                            selected = tab == t,
                            onClick = {},
                            icon = { Icon(t.icon, contentDescription = null) },
                            label = { Text(stringResource(t.label), style = MaterialTheme.typography.labelMedium) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = Huis.colors.ink,
                                selectedTextColor = Huis.colors.ink,
                                indicatorColor = Huis.colors.surfaceMuted,
                                unselectedIconColor = Huis.colors.muted,
                                unselectedTextColor = Huis.colors.muted,
                            ),
                        )
                    }
                }
            },
        ) { padding -> Box(Modifier.padding(padding).consumeWindowInsets(padding)) { content() } }
    }

    /**
     * Normale schermhoogte; met [tall] ook drie delen van een 3× zo hoge weergave (Paparazzi verkleint
     * elke afdruk tot max. 1000 px, daarom in delen; buiten de test aan elkaar gezet).
     */
    private fun shot(name: String, tall: Boolean = true, content: @Composable () -> Unit) {
        paparazzi.snapshot(name = "$name-$theme-$width") { HuisTheme(dark = dark) { content() } }
        if (!tall) return
        for (k in 0..2) {
            paparazzi.snapshot(name = "$name-volledig-deel$k-$theme-$width") {
                HuisTheme(dark = dark) {
                    val h = androidx.compose.ui.platform.LocalConfiguration.current.screenHeightDp
                    Box(Modifier.fillMaxSize().clipToBounds()) {
                        Box(
                            Modifier
                                .wrapContentHeight(align = Alignment.Top, unbounded = true)
                                .requiredHeight((h * 3).dp)
                                .offset(y = (-k * h).dp),
                        ) { content() }
                    }
                }
            }
        }
    }

    @Test fun start() = shot("start") {
        Shell(Tab.START) { HomeScreen(state = online(), vm = vm, onOpenZone = {}, onOpenAlerts = {}, notif = notif) }
    }

    @Test fun startOffline() = shot("start-offline") {
        Shell(Tab.START) { HomeScreen(state = offline(), vm = vm, onOpenZone = {}, onOpenAlerts = {}, notif = notif) }
    }

    @Test fun startKijker() = shot("start-kijker", tall = false) {
        Shell(Tab.START) { HomeScreen(state = online("viewer"), vm = vm, onOpenZone = {}, onOpenAlerts = {}, notif = notif) }
    }

    @Test fun startMeldingenUitleg() = shot("start-meldingen-uitleg", tall = false) {
        Shell(Tab.START) {
            HomeScreen(state = online(), vm = vm, onOpenZone = {}, onOpenAlerts = {}, notif = notif.copy(canPost = false, prompt = NotifPrompt.EXPLAIN))
        }
    }

    @Test fun klimaat() = shot("klimaat") {
        val z = overview.zones.first { it.name == "Woonkamer" }
        Shell(Tab.CLIMATE) { ClimateScreen(state = online(), vm = vm, selectedZoneId = z.id, onSelectZone = {}, onBack = {}) }
    }

    @Test fun klimaatUitConflict() = shot("klimaat-uit-conflict") {
        val z = overview.zones.first { it.name == "Slaapkamer" }
        Shell(Tab.CLIMATE) { ClimateScreen(state = online(), vm = vm, selectedZoneId = z.id, onSelectZone = {}, onBack = {}) }
    }

    @Test fun klimaatOffline() = shot("klimaat-offline", tall = false) {
        val z = overview.zones.first { it.name == "Woonkamer" }
        Shell(Tab.CLIMATE) { ClimateScreen(state = offline(), vm = vm, selectedZoneId = z.id, onSelectZone = {}, onBack = {}) }
    }

    @Test fun energie() = shot("energie") {
        Shell(Tab.ENERGY) { EnergyScreen(state = online(), vm = vm) }
    }

    @Test fun meer() = shot("meer") {
        Shell(Tab.MORE) { MoreScreen(state = online(), vm = vm, notif = notif) }
    }

    @Test fun nietMeerGekoppeld() = shot("niet-meer-gekoppeld", tall = false) {
        UnpairedScreen(online().copy(unpaired = UnpairReason.REVOKED, connection = ConnectionStatus.UNPAIRED, online = false), onRepair = {})
    }

    @Test fun koppelen() = shot("koppelen", tall = false) {
        val registry = object : androidx.activity.result.ActivityResultRegistry() {
            override fun <I, O> onLaunch(
                requestCode: Int,
                contract: androidx.activity.result.contract.ActivityResultContract<I, O>,
                input: I,
                options: androidx.core.app.ActivityOptionsCompat?,
            ) {}
        }
        val owner = object : androidx.activity.result.ActivityResultRegistryOwner {
            override val activityResultRegistry = registry
        }
        androidx.compose.runtime.CompositionLocalProvider(androidx.activity.compose.LocalActivityResultRegistryOwner provides owner) {
            PairScreen(vm, storeWasReset = false)
        }
    }
}

class PeilVoorLicht393 : PeilVoorShots(false, DeviceConfig.PIXEL_5, 393)
class PeilVoorDonker393 : PeilVoorShots(true, DeviceConfig.PIXEL_5, 393)
class PeilVoorLicht360 : PeilVoorShots(false, DeviceConfig.NEXUS_5, 360)
class PeilVoorDonker360 : PeilVoorShots(true, DeviceConfig.NEXUS_5, 360)
