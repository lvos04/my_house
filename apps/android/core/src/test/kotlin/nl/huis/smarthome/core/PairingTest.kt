package nl.huis.smarthome.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingTest {
    @Test
    fun parsesValidPayload() {
        val text = """{"v":1,"type":"shc-pair","code":"k7q4-9xpm","lan":"http://192.168.1.20:8080/","remote":"https://huis.voorbeeld.nl","home":"Thuis"}"""
        val r = PairingCodec.parse(text) as PairParse.Ok
        assertEquals("K7Q4-9XPM", r.code)
        assertEquals("http://192.168.1.20:8080", r.lanUrl)
        assertEquals("https://huis.voorbeeld.nl", r.remoteUrl)
        assertEquals("Thuis", r.homeName)
    }

    @Test
    fun remoteMayBeNull() {
        val r = PairingCodec.parse("""{"v":1,"type":"shc-pair","code":"ABCD-1234","lan":"http://10.0.0.5:8080","remote":null,"home":"Thuis"}""")
        assertTrue(r is PairParse.Ok)
        assertNull((r as PairParse.Ok).remoteUrl)
    }

    @Test
    fun rejectsOtherQrCodes() {
        assertTrue(PairingCodec.parse("https://example.com") is PairParse.Invalid)
        assertTrue(PairingCodec.parse("""{"v":1,"type":"wifi","code":"X"}""") is PairParse.Invalid)
        assertTrue(PairingCodec.parse("""{"v":2,"type":"shc-pair","code":"ABCD-1234","lan":"http://a"}""") is PairParse.Invalid)
        assertTrue(PairingCodec.parse("""{"v":1,"type":"shc-pair","code":"ABCD-1234"}""") is PairParse.Invalid)
    }

    @Test
    fun rejectsPlainHttpRemote() {
        val r = PairingCodec.parse("""{"v":1,"type":"shc-pair","code":"ABCD-1234","remote":"http://huis.voorbeeld.nl"}""")
        assertTrue(r is PairParse.Invalid)
    }

    @Test
    fun normalizesBaseUrls() {
        assertEquals("http://192.168.1.20:8080", UrlTools.normalizeBaseUrl("192.168.1.20:8080"))
        assertEquals("https://huis.voorbeeld.nl", UrlTools.normalizeBaseUrl(" https://huis.voorbeeld.nl/api/v1/ "))
        assertEquals("https://huis.voorbeeld.nl", UrlTools.normalizeBaseUrl("https://huis.voorbeeld.nl:443"))
        assertEquals("http://nas.local:8080/huis", UrlTools.normalizeBaseUrl("http://nas.local:8080/huis/"))
        assertNull(UrlTools.normalizeBaseUrl("ftp://x"))
        assertNull(UrlTools.normalizeBaseUrl(""))
    }

    @Test
    fun buildsApiAndWebSocketUrls() {
        assertEquals("http://h:8080/api/v1/zones/1", UrlTools.api("http://h:8080", "/zones/1"))
        assertEquals("wss://huis.nl/api/v1/ws?home_id=abc", UrlTools.webSocketUrl("https://huis.nl", "abc"))
        assertEquals("ws://h:8080/api/v1/ws", UrlTools.webSocketUrl("http://h:8080/", null))
    }

    @Test
    fun normalizesManualCodes() {
        assertEquals("K7Q4-9XPM", PairingCodec.normalizeCode(" k7q4-9xpm "))
        assertNull(PairingCodec.normalizeCode("ab"))
        assertNull(PairingCodec.normalizeCode("abcd/1234"))
    }
}
