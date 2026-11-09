package nl.huis.smarthome.core

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException

class NetworkTest {
    private val sid = "6f1c2e8a-1b2c-4d5e-8f90-123456789abc"
    private val other = "00000000-1111-2222-3333-444444444444"
    private val lan = "http://192.168.1.20:8088"
    private val remote = "https://huis.voorbeeld.nl"

    @Test
    fun privateHosts() {
        listOf("192.168.1.20", "10.0.0.5", "172.16.0.1", "172.31.255.254", "169.254.1.1", "nas.local", "nas.lan",
            "nas.home.arpa", "nas.internal", "nas.localdomain", "nas", "NAS.LOCAL.", "fd12:3456::1", "[fe80::1]", "::ffff:192.168.1.2")
            .forEach { assertTrue(it, HostRules.isPrivateHost(it)) }
        listOf("8.8.8.8", "172.32.0.1", "127.0.0.1", "localhost", "huis.voorbeeld.nl", "3232235777", "192.168.1",
            "0x7f000001", "2001:db8::1", "::1", "", "100.64.0.1", "192.168.001.020")
            .forEach { assertFalse(it, HostRules.isPrivateHost(it)) }
    }

    @Test
    fun serverAddressRules() {
        assertNull(ServerUrls.lanProblem("http://192.168.1.20:8088"))
        assertNull(ServerUrls.lanProblem("https://nas.local"))
        assertTrue(ServerUrls.lanProblem("http://huis.voorbeeld.nl")!!.contains("thuisnetwerk"))
        assertTrue(ServerUrls.lanProblem("http://127.0.0.1:8088") != null)
        assertNull(ServerUrls.remoteProblem("https://huis.voorbeeld.nl"))
        assertTrue(ServerUrls.remoteProblem("http://huis.voorbeeld.nl")!!.contains("https"))
        assertTrue(ServerUrls.remoteProblem("https://192.168.1.20") != null)
        assertTrue(ServerUrls.remoteProblem("https://localhost") != null)
        assertTrue(ServerUrls.cleartextAllowed("http://192.168.1.20:8088"))
        assertFalse(ServerUrls.cleartextAllowed("http://huis.voorbeeld.nl"))
        assertTrue(ServerUrls.cleartextAllowed("https://huis.voorbeeld.nl"))
    }

    @Test
    fun identityRule() {
        assertEquals(IdentityVerdict.MATCH, ServerIdentityCheck.verify(sid, sid.uppercase()))
        assertEquals(IdentityVerdict.MISMATCH, ServerIdentityCheck.verify(sid, other))
        assertEquals(IdentityVerdict.NOT_A_SERVER, ServerIdentityCheck.verify(sid, null))
        assertEquals(IdentityVerdict.NOT_A_SERVER, ServerIdentityCheck.verify(sid, "<html>"))
        assertEquals(IdentityVerdict.UNKNOWN_EXPECTED, ServerIdentityCheck.verify(null, sid))
    }

    @Test
    fun picksLanFirstThenRemoteWithIdentity() = runTest {
        val probed = mutableListOf<RouteKind>()
        val viaLan = RouteSelector.select(lan, remote, sid) { _, k -> probed += k; Probe.Answered(sid) }
        assertEquals(Route(RouteKind.LAN, lan, sid), viaLan.route)
        assertEquals(listOf(RouteKind.LAN), probed)

        val viaRemote = RouteSelector.select(lan, remote, sid) { _, k ->
            if (k == RouteKind.LAN) Probe.Unreachable else Probe.Answered(sid)
        }
        assertEquals(RouteKind.REMOTE, viaRemote.route?.kind)
        assertFalse(viaRemote.wrongServer)

        val none = RouteSelector.select(lan, null, sid) { _, _ -> Probe.Unreachable }
        assertNull(none.route)
        assertFalse(none.wrongServer)
    }

    @Test
    fun wrongServerOnTheHomeAddressNeverGetsTheToken() = runTest {
        // Another device answers on the same IP and port (foreign wifi): fall back to the tunnel.
        val choice = RouteSelector.select(lan, remote, sid) { _, k ->
            if (k == RouteKind.LAN) Probe.Answered(other) else Probe.Answered(sid)
        }
        assertEquals(RouteKind.REMOTE, choice.route?.kind)

        // No tunnel reachable: no route at all, and the UI says "Dit is niet jouw server".
        val stuck = RouteSelector.select(lan, remote, sid) { _, k ->
            if (k == RouteKind.LAN) Probe.Answered(null) else Probe.Unreachable
        }
        assertNull(stuck.route)
        assertTrue(stuck.wrongServer)
        assertEquals(FailureKind.WRONG_SERVER, Failures.classify(NoRouteException(stuck)))
        assertTrue(NoRouteException(stuck).message!!.contains("niet jouw server"))
    }

    @Test
    fun skipsAddressesThatBreakTheRules() = runTest {
        val probed = mutableListOf<String>()
        val choice = RouteSelector.select("http://huis.voorbeeld.nl", "http://huis.voorbeeld.nl", sid) { b, _ -> probed += b; Probe.Answered(sid) }
        assertNull(choice.route)
        assertTrue(probed.isEmpty())
    }

    @Test
    fun legacyPairingLearnsIdentityOnlyOverHttps() = runTest {
        val lanOnly = RouteSelector.select(lan, null, null) { _, _ -> Probe.Answered(sid) }
        assertNull(lanOnly.route)
        assertTrue(lanOnly.unverifiable)

        val learned = RouteSelector.select(lan, remote, null) { _, _ -> Probe.Answered(sid) }
        assertEquals(RouteKind.REMOTE, learned.route?.kind)
        assertEquals(sid, learned.learnedServerId)
        assertTrue(learned.route!!.verified)
    }

    @Test
    fun cloudflareAccessBlockIsReported() = runTest {
        val choice = RouteSelector.select(null, remote, sid) { _, _ -> Probe.Blocked("Cloudflare Access laat de app niet door.") }
        assertNull(choice.route)
        assertEquals("Cloudflare Access laat de app niet door.", choice.blockedReason)
        assertFalse(choice.wrongServer)
    }

    @Test
    fun probeResultsFromFailures() {
        assertEquals(Probe.Unreachable, Probe.fromFailure(IOException("timeout")))
        assertEquals(Probe.Unreachable, Probe.fromFailure(ApiClient.parseError(530, "<html>1033</html>")))
        assertEquals(Probe.Unreachable, Probe.fromFailure(ApiClient.parseError(503, """{"detail":{"code":"api_unavailable","message":"Start op"}}""")))
        assertEquals(Probe.Answered(null), Probe.fromFailure(ApiClient.parseError(404, """{"detail":"Not Found"}""")))
        assertEquals(Probe.Answered(null), Probe.fromFailure(ApiException(200, "not_api", "html")))
        assertEquals(Probe.Answered(null), Probe.fromFailure(ApiClient.parseError(302, "", "http://wifi.login/portal")))
    }

    @Test
    fun routeIsForgottenAndPickedAgain() = runTest {
        var lanUp = true
        var probes = 0
        val rm = RouteManager({ RouteManager.RouteTarget(lan, remote, sid) }) { _, k ->
            probes++
            if (k == RouteKind.LAN && !lanUp) Probe.Unreachable else Probe.Answered(sid)
        }
        assertEquals(RouteKind.LAN, rm.choose().route?.kind)
        assertEquals(RouteKind.LAN, rm.choose().route?.kind)
        assertEquals(1, probes) // remembered

        // Network change: forgotten, picked again (now away from home).
        lanUp = false
        assertTrue(rm.forget())
        assertEquals(RouteKind.REMOTE, rm.choose().route?.kind)

        // Forgetting a route that is no longer current does nothing.
        assertFalse(rm.forget(Route(RouteKind.LAN, lan, sid)))
        assertEquals(RouteKind.REMOTE, rm.route?.kind)

        // Back home: forget on the network change, LAN first again.
        lanUp = true
        rm.forget()
        assertEquals(RouteKind.LAN, rm.choose().route?.kind)
    }

    @Test
    fun adoptsTheRouteUsedForPairingAndReportsChoices() = runTest {
        val choices = mutableListOf<RouteChoice>()
        val rm = RouteManager({ RouteManager.RouteTarget(lan, remote, sid) }, onChoice = { choices += it }) { _, _ -> Probe.Answered(other) }
        rm.adopt(Route(RouteKind.REMOTE, remote, sid))
        assertEquals(RouteKind.REMOTE, rm.choose().route?.kind)
        assertTrue(choices.isEmpty())
        rm.forget()
        val c = rm.choose()
        assertNull(c.route)
        assertTrue(c.wrongServer)
        assertEquals(listOf(c), choices)
    }

    @Test
    fun forgetDuringProbeDiscardsTheAnswer() = runTest {
        lateinit var rm: RouteManager
        rm = RouteManager({ RouteManager.RouteTarget(lan, null, sid) }) { _, _ ->
            rm.forget() // network changed while we were asking
            Probe.Answered(sid)
        }
        assertEquals(RouteKind.LAN, rm.choose().route?.kind) // used once
        assertNull(rm.route) // but not kept
    }

    private class FakeRoutes(val routes: MutableList<Route?>) : RouteSource {
        var current: Route? = null
        val forgotten = mutableListOf<Route>()
        override suspend fun choose(): RouteChoice {
            current?.let { return RouteChoice(it) }
            current = routes.removeFirstOrNull()
            return RouteChoice(current)
        }

        override fun forget(route: Route?): Boolean {
            val c = current ?: return false
            if (route != null && route != c) return false
            forgotten += c
            current = null
            return true
        }
    }

    @Test
    fun routedRetriesOnceOnNetworkOrServerErrors() = runTest {
        val lanRoute = Route(RouteKind.LAN, lan, sid)
        val remoteRoute = Route(RouteKind.REMOTE, remote, sid)
        val src = FakeRoutes(mutableListOf(lanRoute, remoteRoute))
        val used = mutableListOf<RouteKind>()
        val result = Routed.call(src) { r ->
            used += r.kind
            if (r.kind == RouteKind.LAN) throw IOException("weg") else "ok"
        }
        assertEquals("ok", result)
        assertEquals(listOf(RouteKind.LAN, RouteKind.REMOTE), used)
        assertEquals(listOf(lanRoute), src.forgotten)

        // A 530 from Cloudflare counts as offline, too.
        val src2 = FakeRoutes(mutableListOf(remoteRoute, lanRoute))
        val r2 = Routed.call(src2) { r -> if (r.kind == RouteKind.REMOTE) throw ApiClient.parseError(530, "") else 1 }
        assertEquals(1, r2)
    }

    @Test
    fun routedDoesNotRetryAppErrorsAndGivesUpAfterOneRetry() = runTest {
        val r = Route(RouteKind.LAN, lan, sid)
        var calls = 0
        val src = FakeRoutes(mutableListOf(r, r, r))
        try {
            Routed.call(src) { calls++; throw ApiClient.parseError(409, """{"detail":{"code":"mode_required","message":"Kies eerst een modus."}}""") }
            fail("expected")
        } catch (e: ApiException) {
            assertEquals("mode_required", e.code)
        }
        assertEquals(1, calls)
        assertTrue(src.forgotten.isEmpty())

        calls = 0
        val src2 = FakeRoutes(mutableListOf(r, r, r))
        try {
            Routed.call(src2) { calls++; throw IOException("weg") }
            fail("expected")
        } catch (e: IOException) {
            assertEquals("weg", e.message)
        }
        assertEquals(2, calls)
        assertEquals(2, src2.forgotten.size) // the next call picks again

        try {
            Routed.call(FakeRoutes(mutableListOf(null))) { "never" }
            fail("expected")
        } catch (e: NoRouteException) {
            assertEquals(FailureKind.NO_ROUTE, Failures.classify(e))
        }
    }

    @Test
    fun messagesOnlyOnStatusChange() {
        assertNull(ConnectionText.transition(ConnectionStatus.OFFLINE, ConnectionStatus.OFFLINE))
        assertEquals("Verbinding met je huis verbroken.", ConnectionText.transition(ConnectionStatus.ONLINE, ConnectionStatus.OFFLINE))
        assertEquals("Weer verbonden met je huis.", ConnectionText.transition(ConnectionStatus.OFFLINE, ConnectionStatus.ONLINE))
        assertNull(ConnectionText.transition(ConnectionStatus.CONNECTING, ConnectionStatus.ONLINE))
        assertNull(ConnectionText.transition(ConnectionStatus.CONNECTING, ConnectionStatus.OFFLINE))
        assertTrue(ConnectionText.transition(ConnectionStatus.ONLINE, ConnectionStatus.WRONG_SERVER)!!.contains("niet jouw server"))
        assertEquals(ConnectionStatus.OFFLINE, ConnectionStatus.from(FailureKind.SERVER_DOWN))
        assertEquals(ConnectionStatus.STARTING, ConnectionStatus.from(FailureKind.STARTING))
        assertNull(ConnectionStatus.from(FailureKind.APP_ERROR))
        assertNull(ConnectionText.banner(ConnectionStatus.ONLINE))
    }

    @Test
    fun addressesFromServerSettings() {
        val cur = Addresses(lan, remote)
        assertNull(AddressSync.merge(cur, lan, remote))
        assertEquals(Addresses("http://192.168.1.30:8088", remote), AddressSync.merge(cur, "http://192.168.1.30:8088/", remote))
        assertEquals(Addresses(lan, "https://nieuw.voorbeeld.nl"), AddressSync.merge(cur, null, "https://nieuw.voorbeeld.nl"))
        // Invalid or empty values never replace or clear a working address.
        assertNull(AddressSync.merge(cur, "http://huis.voorbeeld.nl", "http://nieuw.voorbeeld.nl"))
        assertNull(AddressSync.merge(cur, null, null))
        assertEquals(Addresses(lan, "https://huis.voorbeeld.nl"), AddressSync.merge(Addresses(lan, null), "", "https://huis.voorbeeld.nl"))
    }

    @Test
    fun detectsNetworkChanges() {
        val d = NetworkChangeDetector()
        assertFalse(d.onAvailable(100)) // first report: the current network
        assertFalse(d.onAvailable(100)) // same network reported again
        assertTrue(d.onLost(100)) // wifi lost
        assertTrue(d.onAvailable(200)) // mobile data
        assertFalse(d.onLost(100)) // an old network going away is not a change
        assertFalse(d.onAvailable(200))
        assertTrue(d.onAvailable(100)) // home again: wifi becomes the default
    }
}
