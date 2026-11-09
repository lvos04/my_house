package nl.huis.smarthome.peil

import androidx.activity.compose.LocalActivityResultRegistryOwner
import androidx.activity.result.ActivityResultRegistry
import androidx.activity.result.ActivityResultRegistryOwner
import androidx.activity.result.contract.ActivityResultContract
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredHeight
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityOptionsCompat
import app.cash.paparazzi.DeviceConfig
import app.cash.paparazzi.Paparazzi
import com.android.resources.NightMode
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import nl.huis.smarthome.core.CommandLabel
import nl.huis.smarthome.core.ConnectionStatus
import nl.huis.smarthome.core.EnergySummary
import nl.huis.smarthome.core.Overview
import nl.huis.smarthome.core.Quality
import nl.huis.smarthome.core.RealtimeState
import nl.huis.smarthome.core.Route
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.core.ShcJson
import nl.huis.smarthome.core.UnpairReason
import nl.huis.smarthome.core.ZoneSchedule
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.data.Pairing
import nl.huis.smarthome.ui.CommandLine
import nl.huis.smarthome.ui.Ernst
import nl.huis.smarthome.ui.HuisSchil
import nl.huis.smarthome.ui.MainViewModel
import nl.huis.smarthome.ui.MeldingSoort
import nl.huis.smarthome.ui.NotifAction
import nl.huis.smarthome.ui.NotifPrompt
import nl.huis.smarthome.ui.NotifUi
import nl.huis.smarthome.ui.PairUi
import nl.huis.smarthome.ui.PeilStatus
import nl.huis.smarthome.ui.Tab
import nl.huis.smarthome.ui.Tone
import nl.huis.smarthome.ui.components.CommandStatusText
import nl.huis.smarthome.ui.components.ErnstLabel
import nl.huis.smarthome.ui.components.KnopSoort
import nl.huis.smarthome.ui.components.Note
import nl.huis.smarthome.ui.components.PeilKnop
import nl.huis.smarthome.ui.components.QualityLabel
import nl.huis.smarthome.ui.components.SectionTitle
import nl.huis.smarthome.ui.components.StatusBadge
import nl.huis.smarthome.ui.screens.ClimateScreen
import nl.huis.smarthome.ui.screens.EnergyScreen
import nl.huis.smarthome.ui.screens.HomeScreen
import nl.huis.smarthome.ui.screens.MoreScreen
import nl.huis.smarthome.ui.screens.PairScreen
import nl.huis.smarthome.ui.screens.UnpairedScreen
import nl.huis.smarthome.ui.theme.Huis
import nl.huis.smarthome.ui.theme.HuisTheme
import nl.huis.smarthome.ui.theme.LocalHuisKlok
import org.junit.Rule
import org.junit.Test
import org.mockito.Mockito
import java.time.Clock
import java.time.Instant
import java.time.ZoneId

/**
 * Screenshots of the Android app in the design language Peil (docs/ontwerp/PEIL_MIGRATIE.md §11, §12):
 * the real screens and the real shell ([HuisSchil]), light and dark, 360 and 393 dp, with the same
 * names as the "voor" set in docs/screenshots/peil/voor/android/.
 *
 * Data: JSON from the real API of the fictitious test set-up behind the web screenshots
 * (src/test/resources/peil, decoded with the app's own ShcJson); no NAS, device or account data.
 * The clock is fixed, so relative times and the greeting are the same on every run.
 *
 *   ./gradlew :app:recordPaparazziDebug   # write src/test/snapshots/images (the reference)
 *   ./gradlew :app:verifyPaparazziDebug   # compare (CI)
 *
 * With the environment variable PEIL_VOLLEDIG=1 the long screens are also drawn three screens tall
 * in three parts ("-volledig-deel0..2", for docs/screenshots; not part of the reference set).
 */
abstract class PeilAfdrukken(private val dark: Boolean, device: DeviceConfig, private val width: Int) {
    @get:Rule
    val paparazzi = Paparazzi(
        deviceConfig = device.copy(nightMode = if (dark) NightMode.NIGHT else NightMode.NOTNIGHT),
        // Room for anti-aliasing differences between JDK builds; a changed colour or layout is far more.
        maxPercentDifference = 0.5,
    )

    private val theme = if (dark) "donker" else "licht"

    /** 9 Nov 2026, 16:06:30 in Amsterdam: 30 s after the fixtures were taken from the API. */
    private val nu: Instant = Instant.parse("2026-11-09T15:06:30Z")
    private val klok: Clock = Clock.fixed(nu, ZoneId.of("Europe/Amsterdam"))

    private fun res(name: String): String = javaClass.getResource("/peil/$name")!!.readText()
    private val overview: Overview = ShcJson.decodeFromString(Overview.serializer(), res("overview.json"))
    private val energy: EnergySummary = ShcJson.decodeFromString(EnergySummary.serializer(), res("energy-day.json"))
    private val schedule: ZoneSchedule = ShcJson.decodeFromString(ZoneSchedule.serializer(), res("schedule-woonkamer.json"))

    private fun pairing(role: String = "control") = Pairing(
        token = "fictief", lanUrl = "http://192.168.1.20:8088", remoteUrl = null, homeId = overview.home.id,
        homeName = overview.home.name, username = "beheerder", role = "admin", serverId = "peil-test",
        clientRole = role, canControl = role != "viewer", isAdmin = role == "admin",
    )

    private fun online(role: String = "control", o: Overview = overview) = HomeState(
        pairing = pairing(role), overview = o, updatedAt = nu.minusSeconds(20),
        route = Route(RouteKind.LAN, "http://192.168.1.20:8088", "peil-test"), online = true,
        connection = ConnectionStatus.ONLINE, realtime = RealtimeState.CONNECTED, energy = energy,
        schedules = o.zones.firstOrNull { it.name == "Woonkamer" }?.let { mapOf(it.id to schedule) }.orEmpty(),
    )

    private fun offline() = online().copy(
        online = false, connection = ConnectionStatus.OFFLINE, realtime = RealtimeState.DISCONNECTED,
        updatedAt = nu.minusSeconds(7 * 60),
    )

    /** The worker gives no heartbeat: Start shows "Storing in het systeem" (Storing, octagon-x). */
    private fun storing() = online(
        o = overview.copy(
            status = overview.status.map {
                if (it.key == "worker") it.copy(state = "error", detail = "Geen hartslag sinds 14:58", ageS = 500.0) else it
            },
        ),
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

    @Composable
    private fun Thema(content: @Composable () -> Unit) {
        CompositionLocalProvider(LocalHuisKlok provides klok) { HuisTheme(dark = dark) { content() } }
    }

    private val volledig = System.getenv("PEIL_VOLLEDIG") == "1"

    /**
     * Normal screen height; with [tall] and PEIL_VOLLEDIG=1 also three parts of a view three screens
     * tall (Paparazzi scales every picture down to at most 1000 px, so in parts; joined outside the test).
     */
    private fun shot(name: String, tall: Boolean = true, content: @Composable () -> Unit) {
        paparazzi.snapshot(name = "$name-$theme-$width") { Thema { content() } }
        if (!tall || !volledig) return
        for (k in 0..2) {
            paparazzi.snapshot(name = "$name-volledig-deel$k-$theme-$width") {
                Thema {
                    val h = LocalConfiguration.current.screenHeightDp
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
        HuisSchil(Tab.START, onTab = {}) { HomeScreen(state = online(), vm = vm, onOpenZone = {}, onOpenAlerts = {}, notif = notif) }
    }

    @Test fun startOffline() = shot("start-offline") {
        HuisSchil(Tab.START, onTab = {}) { HomeScreen(state = offline(), vm = vm, onOpenZone = {}, onOpenAlerts = {}, notif = notif) }
    }

    @Test fun startKijker() = shot("start-kijker", tall = false) {
        HuisSchil(Tab.START, onTab = {}) { HomeScreen(state = online("viewer"), vm = vm, onOpenZone = {}, onOpenAlerts = {}, notif = notif) }
    }

    @Test fun startMeldingenUitleg() = shot("start-meldingen-uitleg", tall = false) {
        HuisSchil(Tab.START, onTab = {}) {
            HomeScreen(state = online(), vm = vm, onOpenZone = {}, onOpenAlerts = {}, notif = notif.copy(canPost = false, prompt = NotifPrompt.EXPLAIN))
        }
    }

    @Test fun startStoring() = shot("start-storing", tall = false) {
        HuisSchil(Tab.START, onTab = {}) { HomeScreen(state = storing(), vm = vm, onOpenZone = {}, onOpenAlerts = {}, notif = notif) }
    }

    @Test fun klimaat() = shot("klimaat") {
        val z = overview.zones.first { it.name == "Woonkamer" }
        HuisSchil(Tab.CLIMATE, onTab = {}) { ClimateScreen(state = online(), vm = vm, selectedZoneId = z.id, onSelectZone = {}, onBack = {}) }
    }

    @Test fun klimaatUitConflict() = shot("klimaat-uit-conflict") {
        val z = overview.zones.first { it.name == "Slaapkamer" }
        HuisSchil(Tab.CLIMATE, onTab = {}) { ClimateScreen(state = online(), vm = vm, selectedZoneId = z.id, onSelectZone = {}, onBack = {}) }
    }

    @Test fun klimaatOffline() = shot("klimaat-offline", tall = false) {
        val z = overview.zones.first { it.name == "Woonkamer" }
        HuisSchil(Tab.CLIMATE, onTab = {}) { ClimateScreen(state = offline(), vm = vm, selectedZoneId = z.id, onSelectZone = {}, onBack = {}) }
    }

    @Test fun energie() = shot("energie") {
        HuisSchil(Tab.ENERGY, onTab = {}) { EnergyScreen(state = online(), vm = vm) }
    }

    @Test fun meer() = shot("meer") {
        HuisSchil(Tab.MORE, onTab = {}) { MoreScreen(state = online(), vm = vm, notif = notif) }
    }

    @Test fun nietMeerGekoppeld() = shot("niet-meer-gekoppeld", tall = false) {
        UnpairedScreen(online().copy(unpaired = UnpairReason.REVOKED, connection = ConnectionStatus.UNPAIRED, online = false), onRepair = {})
    }

    @Test fun koppelen() = shot("koppelen", tall = false) {
        val registry = object : ActivityResultRegistry() {
            override fun <I, O> onLaunch(requestCode: Int, contract: ActivityResultContract<I, O>, input: I, options: ActivityOptionsCompat?) {}
        }
        val owner = object : ActivityResultRegistryOwner {
            override val activityResultRegistry = registry
        }
        CompositionLocalProvider(LocalActivityResultRegistryOwner provides owner) { PairScreen(vm, storeWasReset = false) }
    }

    /** Peil building blocks: status, severity, measurement quality and command lines. */
    @OptIn(ExperimentalLayoutApi::class)
    @Test fun onderdelen() = shot("peil-onderdelen", tall = false) {
        Column(
            Modifier
                .fillMaxSize()
                .background(Huis.peil.background)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SectionTitle("Status")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                PeilStatus.entries.forEach { StatusBadge(it) }
            }
            SectionTitle("Ernst en meetkwaliteit")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Ernst.entries.forEach { ErnstLabel(it) }
                Quality.entries.forEach { QualityLabel(it) }
            }
            SectionTitle("Opdrachten")
            CommandStatusText(CommandLine.Sending)
            listOf(CommandLabel.CONFIRMED, CommandLabel.TIMEOUT).forEach { CommandStatusText(CommandLine.Tracked(it, null, Tone.MUTED)) }
            CommandStatusText(CommandLine.Tracked(CommandLabel.DENIED, "buitenunit koelt al", Tone.WARN))
        }
    }

    /** Notes (Melding) of every kind and the four buttons, enabled and disabled. */
    @OptIn(ExperimentalLayoutApi::class)
    @Test fun onderdelenMeldingenKnoppen() = shot("peil-onderdelen-meldingen", tall = false) {
        Column(
            Modifier
                .fillMaxSize()
                .background(Huis.peil.background)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SectionTitle("Meldingen")
            MeldingSoort.entries.forEach { Note("Melding: ${it.name.lowercase()}", it) }
            SectionTitle("Knoppen")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                KnopSoort.entries.forEach { PeilKnop(it.name.lowercase().replaceFirstChar(Char::titlecase), onClick = {}, soort = it) }
                PeilKnop("Uitgeschakeld", onClick = {}, soort = KnopSoort.PRIMAIR, enabled = false)
                PeilKnop("Uitgeschakeld", onClick = {}, soort = KnopSoort.SECUNDAIR, enabled = false)
            }
        }
    }
}

class PeilLicht393 : PeilAfdrukken(false, DeviceConfig.PIXEL_5, 393)
class PeilDonker393 : PeilAfdrukken(true, DeviceConfig.PIXEL_5, 393)
class PeilLicht360 : PeilAfdrukken(false, DeviceConfig.NEXUS_5, 360)
class PeilDonker360 : PeilAfdrukken(true, DeviceConfig.NEXUS_5, 360)
