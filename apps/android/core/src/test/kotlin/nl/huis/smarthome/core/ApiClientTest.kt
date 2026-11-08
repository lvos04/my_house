package nl.huis.smarthome.core

import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class ApiClientTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient
    private var creds = Credentials(token = "shc_abc")

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val base = server.url("/").toString().trimEnd('/')
        api = ApiClient(OkHttpClient(), { base }, { creds })
    }

    @After
    fun tearDown() = server.shutdown()

    @Test
    fun redeemSendsCodeWithoutBearer() = runTest {
        server.enqueue(MockResponse().setBody("""{"token":"shc_new","user":{"id":"u1","username":"telefoon","role":"admin"},"server":{"lan_url":"http://192.168.1.20:8080","remote_url":null,"home_id":"h1","home_name":"Thuis"}}"""))
        val r = api.redeem(server.url("/").toString().trimEnd('/'), "K7Q4-9XPM", "Pixel 8")
        assertEquals("shc_new", r.token)
        assertEquals("h1", r.server.homeId)
        assertNull(r.server.remoteUrl)
        val req = server.takeRequest()
        assertEquals("/api/v1/pairing/redeem", req.path)
        assertNull(req.getHeader("Authorization"))
        assertTrue(req.body.readUtf8().contains("\"device_name\":\"Pixel 8\""))
    }

    @Test
    fun sendsBearerAndCloudflareHeaders() = runTest {
        creds = Credentials("shc_abc", "id.access", "secret")
        server.enqueue(MockResponse().setBody("[]"))
        api.zones("h1")
        val req = server.takeRequest()
        assertEquals("/api/v1/homes/h1/zones", req.path)
        assertEquals("Bearer shc_abc", req.getHeader("Authorization"))
        assertEquals("id.access", req.getHeader("CF-Access-Client-Id"))
        assertEquals("secret", req.getHeader("CF-Access-Client-Secret"))
    }

    @Test
    fun parsesOverviewWithUnknownFields() = runTest {
        server.enqueue(MockResponse().setBody(OVERVIEW))
        val o = api.overview("h1")
        assertEquals("Thuis", o.home.name)
        assertEquals(2, o.zones.size)
        val w = o.zones[0]
        assertEquals(20.6, w.temperature.value!!, 0.001)
        assertEquals(Quality.MEASURED, w.temperature.quality)
        assertEquals("De slaapkamer verwarmt", w.blockReason("cool"))
        assertTrue(o.zones[1].temperature.stale)
        assertEquals(Quality.DERIVED, o.energyToday!!.meters.first { it.role == "rest" }.quality)
        assertEquals(1240.0, o.powerNow!!.totalW!!, 0.01)
    }

    @Test
    fun commandEncodesOnlySetFields() = runTest {
        server.enqueue(MockResponse().setResponseCode(202).setBody("""{"command_id":"c1","status":"queued"}"""))
        val r = api.command("z1", ZoneCommand(targetC = 21.5))
        assertEquals("c1", r.commandId)
        val body = server.takeRequest().body.readUtf8()
        assertEquals("""{"target_c":21.5}""", body)
    }

    @Test
    fun mapsPolicyErrorToApiException() = runTest {
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":{"code":"policy_denied","message":"Koelen kan niet terwijl een andere unit verwarmt."}}"""))
        try {
            api.command("z1", ZoneCommand(mode = "cool"))
            fail("expected exception")
        } catch (e: ApiException) {
            assertEquals(409, e.status)
            assertEquals("policy_denied", e.code)
            assertEquals("Koelen kan niet terwijl een andere unit verwarmt.", e.message)
        }
    }

    @Test
    fun mapsPlainErrors() {
        assertEquals("auth_required", ApiClient.parseError(401, """{"detail":{"code":"auth_required","message":""}}""").code)
        assertEquals("Opnieuw koppelen nodig.", ApiClient.parseError(401, "").message)
        assertEquals("Not Found", ApiClient.parseError(404, """{"detail":"Not Found"}""").message)
        assertTrue(ApiClient.parseError(401, "").isAuthError)
    }

    @Test
    fun parsesRealtimeFrames() {
        val z = RealtimeEvent.parse("""{"type":"zone.updated","data":{"id":"z1","name":"Woonkamer","mode":"heat","target_c":21.5}}""")
        assertTrue(z is RealtimeEvent.ZoneUpdated)
        assertEquals(21.5, (z as RealtimeEvent.ZoneUpdated).zone.targetC!!, 0.0)
        val p = RealtimeEvent.parse("""{"type":"energy.live","data":{"at":"2026-11-08T13:40:00Z","total_w":900,"parts":[]}}""")
        assertTrue(p is RealtimeEvent.EnergyLive)
        assertNull(RealtimeEvent.parse("""{"type":"something.new","data":{}}"""))
        assertNull(RealtimeEvent.parse("garbage"))
        assertEquals("""{"type":"auth","token":"shc_x"}""", RealtimeEvent.authFrame("shc_x"))
    }

    companion object {
        val OVERVIEW = """
        {"home":{"id":"h1","name":"Thuis","timezone":"Europe/Amsterdam","currency":"EUR","active":true,"extra":1},
         "status":[{"key":"hue","label":"Hue Bridge","state":"ok","detail":"Verbonden"}],
         "zones":[
          {"id":"z1","name":"Woonkamer","room":"woonkamer","online":true,"mode":"heat","target_c":21.0,"fan":"auto",
           "temperature":{"value":20.6,"unit":"°C","quality":"measured","at":"2026-11-08T13:39:00Z","stale":false},
           "humidity":{"value":54,"unit":"%","quality":"measured","at":"2026-11-08T13:39:00Z","stale":false},
           "control":"schedule","profile":"Comfort","override":null,
           "capabilities":{"modes":["off","heat","cool"],"fan":true,"swing":false,"min_c":16,"max_c":30,"step_c":0.5},
           "blocked_modes":[{"mode":"cool","reason":"De slaapkamer verwarmt"}],
           "next_change":{"at":"2026-11-08T21:30:00Z","target_c":18.5,"label":"Nacht"},
           "policy":{"band_c":0.5,"min_interval_s":900,"max_step_c":1.0,"max_starts_per_hour":3,"stale_after_s":1800},
           "notice":null},
          {"id":"z2","name":"Slaapkamer","online":true,"mode":"heat","target_c":19.0,
           "temperature":{"value":18.9,"unit":"°C","quality":"measured","at":"2026-11-08T12:53:00Z","stale":true},
           "control":"oem_fallback","override":{"until":"2026-11-08T17:00:00Z","source":"remote_control"},
           "notice":"Sensor verouderd"}
         ],
         "lights":[{"id":"g1","name":"Woonkamer","on":true,"brightness":60,"scene":"Avond","reachable":true,"lights_on":3,"lights_total":4}],
         "energy_today":{"period":"day","total_kwh":8.4,"cost":2.18,"currency":"EUR",
           "meters":[{"role":"airco","label":"Airco","kwh":3.1,"quality":"measured"},{"role":"rest","label":"Overig","kwh":3.1,"quality":"derived"}],
           "buckets":[{"start":"2026-11-08T00:00:00Z","values":{"airco":0.2,"rest":null},"complete":true}]},
         "power_now":{"at":"2026-11-08T13:40:00Z","total_w":1240,"parts":[{"role":"airco","label":"Airco","watts":740,"quality":"measured"}]},
         "alerts":[{"id":"a1","severity":"warn","title":"Sensor slaapkamer verouderd","created_at":"2026-11-08T13:30:00Z","acknowledged":false}]}
        """.trimIndent()
    }
}
