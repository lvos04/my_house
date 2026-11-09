package nl.huis.smarthome.core

import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

class ApiClientTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient
    private var creds = Credentials(token = "shc_abc")
    private lateinit var base: String
    private lateinit var lan: Route
    private lateinit var remote: Route

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        base = server.url("/").toString().trimEnd('/')
        lan = Route(RouteKind.LAN, base, SID)
        remote = Route(RouteKind.REMOTE, base, SID)
        api = ApiClient(OkHttpClient.Builder().followRedirects(false).build(), { creds }, cleartextAllowed = { true })
    }

    @After
    fun tearDown() = server.shutdown()

    @Test
    fun redeemSendsCodeWithoutBearer() = runTest {
        server.enqueue(MockResponse().setBody("""{"token":"shc_new","user":{"id":"u1","username":"telefoon","role":"admin"},"client":{"id":"c1","role":"control","expires_at":"2027-05-07T10:00:00Z"},"server":{"server_id":"$SID","lan_url":"http://192.168.1.20:8080","remote_url":null,"home_id":"h1","home_name":"Thuis"}}"""))
        val r = api.redeem(base, RouteKind.LAN, "K7Q4-9XPM", "Pixel 8")
        assertEquals("shc_new", r.token)
        assertEquals("h1", r.server.homeId)
        assertEquals(SID, r.server.serverId)
        assertEquals("control", r.client!!.role)
        assertEquals("2027-05-07T10:00:00Z", r.client!!.expiresAt)
        assertNull(r.server.remoteUrl)
        val req = server.takeRequest()
        assertEquals("/api/v1/pairing/redeem", req.path)
        assertNull(req.getHeader("Authorization"))
        assertTrue(req.body.readUtf8().contains("\"device_name\":\"Pixel 8\""))
    }

    @Test
    fun sendsBearerAndCloudflareHeadersOnlyToRemote() = runTest {
        creds = Credentials("shc_abc", "id.access", "secret")
        server.enqueue(MockResponse().setBody("[]"))
        api.zones(remote, "h1")
        val req = server.takeRequest()
        assertEquals("/api/v1/homes/h1/zones", req.path)
        assertEquals("Bearer shc_abc", req.getHeader("Authorization"))
        assertEquals("id.access", req.getHeader("CF-Access-Client-Id"))
        assertEquals("secret", req.getHeader("CF-Access-Client-Secret"))

        // The home address never gets the Cloudflare secret.
        server.enqueue(MockResponse().setBody("[]"))
        api.zones(lan, "h1")
        val lanReq = server.takeRequest()
        assertEquals("Bearer shc_abc", lanReq.getHeader("Authorization"))
        assertNull(lanReq.getHeader("CF-Access-Client-Id"))
        assertNull(lanReq.getHeader("CF-Access-Client-Secret"))
    }

    @Test
    fun neverSendsTokenToUnverifiedRoute() = runTest {
        try {
            api.zones(Route(RouteKind.LAN, base), "h1")
            fail("expected exception")
        } catch (e: ApiException) {
            assertEquals("unverified_route", e.code)
        }
        assertEquals(0, server.requestCount)
    }

    @Test
    fun refusesTokenOverCleartextToPublicHost() = runTest {
        val strict = ApiClient(OkHttpClient(), { creds })
        try {
            strict.zones(Route(RouteKind.LAN, "http://example.com", SID), "h1")
            fail("expected exception")
        } catch (e: ApiException) {
            assertEquals("cleartext_refused", e.code)
        }
    }

    @Test
    fun identityIsUnauthenticated() = runTest {
        creds = Credentials("shc_abc", "id.access", "secret")
        server.enqueue(MockResponse().setBody("""{"server_id":"$SID","name":"Thuis"}"""))
        val id = api.identity(base, RouteKind.LAN)
        assertEquals(SID, id.serverId)
        val req = server.takeRequest()
        assertEquals("/api/v1/pairing/identity", req.path)
        assertNull(req.getHeader("Authorization"))
        assertNull(req.getHeader("CF-Access-Client-Id"))

        server.enqueue(MockResponse().setBody("""{"server_id":"$SID","name":"Thuis"}"""))
        api.identity(base, RouteKind.REMOTE)
        val r2 = server.takeRequest()
        assertNull(r2.getHeader("Authorization"))
        assertEquals("id.access", r2.getHeader("CF-Access-Client-Id"))
    }

    @Test
    fun phoneEndpoints() = runTest {
        server.enqueue(MockResponse().setBody("""{"token":"shc_fresh","expires_at":"2027-05-07T10:00:00Z"}"""))
        assertEquals("shc_fresh", api.refreshToken(lan).token)
        assertEquals("/api/v1/clients/self/refresh", server.takeRequest().path)

        server.enqueue(MockResponse().setResponseCode(204))
        api.unpairSelf(lan)
        val del = server.takeRequest()
        assertEquals("DELETE", del.method)
        assertEquals("/api/v1/clients/self", del.path)

        server.enqueue(MockResponse().setBody("""{"lan_url":"http://192.168.1.30:8088","remote_url":"https://huis.voorbeeld.nl","safe_mode":false,"maintenance":false}"""))
        assertEquals("http://192.168.1.30:8088", api.settings(lan).lanUrl)

        server.enqueue(MockResponse().setBody("""{"id":"u1","username":"anna","role":"admin","via":"token","client_role":"viewer","is_admin":false,"can_control":false}"""))
        val me = api.me(lan)
        assertEquals(false, me.canControl)
        assertFalse(me.isAdmin)
    }

    @Test
    fun revokedTokenMeansUnpaired() = runTest {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":{"code":"token_revoked","message":"Deze telefoon is niet meer gekoppeld."}}"""))
        try {
            api.overview(lan, "h1")
            fail("expected exception")
        } catch (e: ApiException) {
            assertEquals(UnpairReason.REVOKED, e.unpairReason)
            assertEquals(FailureKind.UNPAIRED, Failures.classify(e))
        }
    }

    @Test
    fun htmlPageIsNotTheApi() = runTest {
        server.enqueue(MockResponse().setBody("<html>Wifi-login</html>"))
        try {
            api.overview(lan, "h1")
            fail("expected exception")
        } catch (e: ApiException) {
            assertEquals("not_api", e.code)
            assertTrue(e.routeProblem)
            assertEquals(FailureKind.SERVER_DOWN, Failures.classify(e))
        }
    }

    @Test
    fun cloudflareAccessRedirectIsReported() = runTest {
        server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "https://team.cloudflareaccess.com/cdn-cgi/access/login"))
        try {
            api.overview(remote, "h1")
            fail("expected exception")
        } catch (e: ApiException) {
            assertEquals("cf_access_login", e.code)
            assertTrue(e.routeProblem)
            assertTrue(Probe.fromFailure(e) is Probe.Blocked)
        }
    }

    @Test
    fun parsesOverviewWithUnknownFields() = runTest {
        server.enqueue(MockResponse().setBody(OVERVIEW))
        val o = api.overview(lan, "h1")
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
        val r = api.command(lan, "z1", ZoneCommand(targetC = 21.5))
        assertEquals("c1", r.commandId)
        val body = server.takeRequest().body.readUtf8()
        assertEquals("""{"target_c":21.5}""", body)
    }

    @Test
    fun mapsPolicyErrorToApiException() = runTest {
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":{"code":"policy_denied","message":"Koelen kan niet terwijl een andere unit verwarmt."}}"""))
        try {
            api.command(lan, "z1", ZoneCommand(mode = "cool"))
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
        // A bare 401 from a proxy is not the server revoking us.
        assertNull(ApiClient.parseError(401, "").unpairReason)
        assertEquals(UnpairReason.UNKNOWN_TOKEN, ApiClient.parseError(401, """{"detail":{"code":"auth_required","message":"Log in"}}""").unpairReason)
        assertEquals(UnpairReason.EXPIRED, ApiClient.parseError(401, """{"detail":{"code":"token_expired","message":"x"}}""").unpairReason)
        assertEquals(UnpairReason.REVOKED, ApiClient.parseError(403, """{"detail":{"code":"token_revoked","message":"x"}}""").unpairReason)
        assertNull(ApiClient.parseError(403, """{"detail":{"code":"forbidden","message":"Alleen-lezen toegang."}}""").unpairReason)
    }

    @Test
    fun classifiesServerAndCloudflareErrorsAsOffline() {
        val cf530 = ApiClient.parseError(530, "<html>error code: 1033</html>")
        assertEquals("tunnel_unavailable", cf530.code)
        assertEquals(FailureKind.SERVER_DOWN, Failures.classify(cf530))
        assertEquals(FailureKind.SERVER_DOWN, Failures.classify(ApiClient.parseError(502, "<html>Bad gateway</html>")))
        assertEquals(FailureKind.SERVER_DOWN, Failures.classify(ApiClient.parseError(522, "")))
        val starting = ApiClient.parseError(503, """{"detail":{"code":"api_unavailable","message":"De app start op of wordt bijgewerkt."}}""")
        assertEquals(FailureKind.STARTING, Failures.classify(starting))
        assertEquals("De app start op of wordt bijgewerkt.", starting.message)
        assertEquals(FailureKind.SERVER_DOWN, Failures.classify(ApiClient.parseError(500, """{"detail":"Internal Server Error"}""")))
        assertEquals(FailureKind.FORBIDDEN, Failures.classify(ApiClient.parseError(403, """{"detail":{"code":"forbidden","message":"Alleen-lezen toegang."}}""")))
        val modeRequired = ApiClient.parseError(409, """{"detail":{"code":"mode_required","message":"Kies eerst een modus."}}""")
        assertEquals(FailureKind.APP_ERROR, Failures.classify(modeRequired))
        assertEquals("Kies eerst een modus.", modeRequired.message)
        assertEquals(FailureKind.NETWORK, Failures.classify(java.net.SocketTimeoutException("timeout")))
        assertTrue(FailureKind.SERVER_DOWN.repick && FailureKind.NETWORK.repick && FailureKind.STARTING.repick)
        assertFalse(FailureKind.APP_ERROR.repick || FailureKind.UNPAIRED.repick || FailureKind.FORBIDDEN.repick)
    }

    @Test
    fun parsesRealtimeFrames() {
        val z = RealtimeEvent.parse("""{"type":"zone.updated","data":{"id":"z1","name":"Woonkamer","mode":"heat","target_c":21.5,"unit_linked":true,"unit_status":"online","can_resume":true,"setpoint_adjustable":false,"notice_level":"info","pending_command":{"id":"c9","status":"sent","since":"2026-11-08T13:40:00Z"}}}""")
        assertTrue(z is RealtimeEvent.ZoneUpdated)
        val zone = (z as RealtimeEvent.ZoneUpdated).zone
        assertEquals(21.5, zone.targetC!!, 0.0)
        assertTrue(zone.resumable)
        assertFalse(zone.adjustable)
        assertEquals("c9", zone.pendingCommand!!.id)
        val p = RealtimeEvent.parse("""{"type":"energy.live","data":{"at":"2026-11-08T13:40:00Z","total_w":900,"parts":[]}}""")
        assertTrue(p is RealtimeEvent.EnergyLive)
        assertNull(RealtimeEvent.parse("""{"type":"something.new","data":{}}"""))
        assertNull(RealtimeEvent.parse("garbage"))
        assertEquals("""{"type":"auth","token":"shc_x"}""", RealtimeEvent.authFrame("shc_x"))
    }

    companion object {
        const val SID = "6f1c2e8a-1b2c-4d5e-8f90-123456789abc"

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
