package nl.huis.smarthome

import android.content.Context
import android.content.res.Configuration
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import nl.huis.smarthome.core.ApiClient
import nl.huis.smarthome.core.ApiException
import nl.huis.smarthome.core.Credentials
import nl.huis.smarthome.core.GroupCommand
import nl.huis.smarthome.core.Route
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.core.ZoneCommand
import nl.huis.smarthome.data.CoreTexts
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.net.InetAddress
import java.util.Locale
import java.util.UUID

/**
 * Emulatorproef van de bedienweigering (B06-009, docs/testlog.md): de API weigert een
 * bedienopdracht voor een ongetest apparaat met 409 device_not_tested. Op een echt Android-systeem
 * gaat de opdracht over HTTP naar een nepserver op loopback, de app herhaalt haar niet en toont de
 * vertaalde weigering uit strings.xml (nl en en). Alleen testgegevens; geen echt apparaat of NAS.
 */
@RunWith(AndroidJUnit4::class)
class BedienweigeringTest {
    private val server = MockWebServer()
    private val context: Context = InstrumentationRegistry.getInstrumentation().targetContext

    @Before
    fun start() = server.start(InetAddress.getLoopbackAddress(), 0)

    @After
    fun stop() = server.shutdown()

    @Test
    fun ongetestApparaatWordtEenmaalGeweigerdMetVertaaldeUitleg() = runBlocking {
        val api = ApiClient(OkHttpClient(), { Credentials(token = "shc_test_${UUID.randomUUID()}") }, cleartextAllowed = { true })
        val route = Route(RouteKind.LAN, server.url("/").toString().trimEnd('/'), UUID.randomUUID().toString())
        val weigering = """{"detail":{"code":"device_not_tested","message":"Servertekst"}}"""
        val pogingen = listOf<Pair<String, suspend () -> Unit>>(
            "/api/v1/lights/groups/g1" to { api.setGroup(route, "g1", GroupCommand(on = true)) },
            "/api/v1/zones/z1/command" to { api.command(route, "z1", ZoneCommand(targetC = 21.0)) },
        )
        for ((pad, opdracht) in pogingen) {
            server.enqueue(MockResponse().setResponseCode(409).setBody(weigering))
            val fout = runCatching { opdracht() }.exceptionOrNull()
            val e = fout as? ApiException ?: throw AssertionError("Verwacht een bedienweigering voor $pad", fout)
            assertEquals(409, e.status)
            assertEquals("device_not_tested", e.code)
            assertEquals(pad, server.takeRequest().path)
            assertEquals(context.getString(R.string.commissioning_denied), CoreTexts.message(context, e))
            assertTrue(CoreTexts.message(inTaal("nl"), e)!!.endsWith("Er is geen bedienopdracht verstuurd."))
            assertTrue(CoreTexts.message(inTaal("en"), e)!!.endsWith("No control command was sent."))
        }
        // Eén verzoek per opdracht: geen automatische herhaling na de weigering.
        assertEquals(pogingen.size, server.requestCount)
    }

    private fun inTaal(taal: String): Context =
        context.createConfigurationContext(Configuration(context.resources.configuration).apply { setLocale(Locale(taal)) })
}
