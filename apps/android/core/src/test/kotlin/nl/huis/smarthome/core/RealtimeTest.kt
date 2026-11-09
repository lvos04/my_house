package nl.huis.smarthome.core

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger

class RealtimeTest {
    private val sid = "6f1c2e8a-1b2c-4d5e-8f90-123456789abc"

    @Test
    fun parsesAllDocumentedEvents() {
        val status = RealtimeEvent.parse("""{"type":"status.updated","data":[{"key":"worker","label":"Achtergronddienst","state":"ok","detail":"Laatste hartslag 10 s geleden","age_s":10},{"key":"safe_mode"}]}""")
        assertEquals(2, (status as RealtimeEvent.StatusUpdated).status.size)
        assertEquals("", status.status[1].label)
        assertEquals(10.0, status.status[0].ageS!!, 0.0)
        assertNull(status.status[1].ageS)
        val wrapped = RealtimeEvent.parse("""{"type":"status.updated","data":{"status":[{"key":"backup","label":"Back-up","state":"warn","age_h":40.5,"auto_failures":0}]}}""")
        assertEquals("backup", (wrapped as RealtimeEvent.StatusUpdated).status.single().key)
        assertEquals(40.5, wrapped.status.single().ageH!!, 0.0)

        assertEquals(RealtimeEvent.AlertResolved("a1"), RealtimeEvent.parse("""{"type":"alert.resolved","data":{"id":"a1"}}"""))
        val live = RealtimeEvent.parse("""{"type":"energy.live","data":{"at":"2026-11-08T13:40:00Z","total_w":900,"complete":false,"note":"Onvolledig","parts":[{"role":"airco","label":"Airco","watts":null,"quality":"missing","stale":true}]}}""")
        live as RealtimeEvent.EnergyLive
        assertFalse(live.power.complete)
        assertTrue(live.power.parts.single().stale)

        val cmd = RealtimeEvent.parse("""{"type":"command.updated","data":{"id":"c1","status":"queued","detail":"opnieuw","attempts":1,"retrying":true}}""")
        assertEquals(CommandUpdate("c1", "queued", "opnieuw", 1, true), (cmd as RealtimeEvent.CommandUpdated).update)

        assertEquals(RealtimeEvent.Refresh("zone.updated"), RealtimeEvent.parse("""{"type":"refresh","data":{"reason":"zone.updated"}}"""))
        assertEquals(RealtimeEvent.Refresh(null), RealtimeEvent.parse("""{"type":"refresh"}"""))

        val hello = RealtimeEvent.parse("""{"type":"hello","data":{"user":{"id":"u1","username":"anna","role":"admin"},"via":"token","client_role":"viewer","can_control":false}}""")
        assertEquals(false, (hello as RealtimeEvent.HelloReceived).hello.canControl)
        assertEquals("viewer", hello.hello.clientRole)

        val err = RealtimeEvent.parse("""{"type":"error","data":{"code":"token_revoked","reason":"phone_revoked"}}""")
        assertEquals("token_revoked", (err as RealtimeEvent.ServerError).error.code)

        assertNull(RealtimeEvent.parse("""{"type":"integration.changed","data":{"id":"i1"}}"""))
        assertNull(RealtimeEvent.parse("""{"type":"zone.updated","data":[1,2]}"""))
    }

    @Test
    fun cloudflareHeadersOnlyOnTheRemoteSocket() {
        val creds = Credentials("shc_x", "id.access", "secret")
        val rc = RealtimeClient(OkHttpClient(), CoroutineScope(Dispatchers.Unconfined), { null }, { "h1" }, { creds })
        val remote = rc.request(Route(RouteKind.REMOTE, "https://huis.voorbeeld.nl", sid))
        assertEquals("https://huis.voorbeeld.nl/api/v1/ws?home_id=h1", remote.url.toString())
        assertEquals("secret", remote.header("CF-Access-Client-Secret"))
        val lan = rc.request(Route(RouteKind.LAN, "http://192.168.1.20:8088", sid))
        assertEquals("http://192.168.1.20:8088/api/v1/ws?home_id=h1", lan.url.toString())
        assertNull(lan.header("CF-Access-Client-Id"))
        assertNull(lan.header("CF-Access-Client-Secret"))
    }

    private fun waitFor(list: List<RealtimeEvent>, pred: (RealtimeEvent) -> Boolean): RealtimeEvent {
        val deadline = System.currentTimeMillis() + 10_000
        while (System.currentTimeMillis() < deadline) {
            list.firstOrNull(pred)?.let { return it }
            Thread.sleep(20)
        }
        throw AssertionError("event not received: $list")
    }

    /** Server side of a socket: answers the auth frame with [onAuth]. */
    private fun socket(onAuth: (WebSocket, String) -> Unit) = MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
        override fun onMessage(webSocket: WebSocket, text: String) = onAuth(webSocket, text)
        override fun onOpen(webSocket: WebSocket, response: Response) = Unit
        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
        }
    })

    @Test
    fun revokedPhoneStopsReconnecting(): Unit = runBlocking {
        val server = MockWebServer()
        val frames = CopyOnWriteArrayList<String>()
        server.enqueue(socket { ws, text ->
            frames += text
            ws.send("""{"type":"error","data":{"code":"token_expired"}}""")
            ws.close(4403, null)
        })
        server.start()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val base = server.url("/").toString().trimEnd('/')
        val route = Route(RouteKind.LAN, base, sid)
        val attempts = AtomicInteger()
        val rc = RealtimeClient(OkHttpClient(), scope, { attempts.incrementAndGet(); route }, { null }, { Credentials("shc_x") }, backoff = Backoff(10, 20))
        val events = CopyOnWriteArrayList<RealtimeEvent>()
        val collector = scope.launch { rc.events.collect { events += it } }
        rc.start()
        val r = waitFor(events) { it is RealtimeEvent.Rejected } as RealtimeEvent.Rejected
        assertEquals(CloseCodes.REVOKED, r.closeCode)
        assertEquals(UnpairReason.EXPIRED, r.reason)
        assertEquals("""{"type":"auth","token":"shc_x"}""", frames.single())
        Thread.sleep(300)
        assertEquals(1, attempts.get()) // no second attempt with a dead token
        assertFalse(rc.running)
        collector.cancel()
        scope.cancel()
        runCatching { server.shutdown() }
    }

    @Test
    fun connectedOnlyAfterHello(): Unit = runBlocking {
        val server = MockWebServer()
        server.enqueue(socket { ws, _ ->
            ws.send("""{"type":"hello","data":{"via":"token","client_role":"control","can_control":true}}""")
            ws.send("""{"type":"alert.resolved","data":{"id":"a9"}}""")
        })
        server.start()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val route = Route(RouteKind.LAN, server.url("/").toString().trimEnd('/'), sid)
        val rc = RealtimeClient(OkHttpClient(), scope, { route }, { null }, { Credentials("shc_x") })
        val collected = CopyOnWriteArrayList<RealtimeEvent>()
        val job = scope.launch { rc.events.collect { collected += it } }
        rc.start()
        waitFor(collected) { it is RealtimeEvent.AlertResolved }
        assertEquals(RealtimeState.CONNECTED, rc.state.value)
        assertTrue(collected.any { it is RealtimeEvent.HelloReceived })
        assertTrue(collected.contains(RealtimeEvent.Connected))
        rc.stop()
        job.cancel()
        scope.cancel()
        runCatching { server.shutdown() }
    }

    @Test
    fun unverifiedRouteGetsNoSocket(): Unit = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val asked = AtomicInteger()
        val rc = RealtimeClient(OkHttpClient(), scope, { asked.incrementAndGet(); Route(RouteKind.LAN, "http://192.168.1.20:8088") }, { null }, { Credentials("shc_x") }, backoff = Backoff(10, 20))
        rc.start()
        Thread.sleep(200)
        assertTrue(asked.get() >= 2)
        assertEquals(RealtimeState.DISCONNECTED, rc.state.value)
        rc.stop()
        scope.cancel()
    }
}
