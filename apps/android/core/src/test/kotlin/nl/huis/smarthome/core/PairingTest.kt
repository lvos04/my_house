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

    @Test
    fun readsServerIdAndRole() {
        val text = """{"v":1,"type":"shc-pair","code":"K7Q4-9XPM","lan":"http://192.168.1.20:8088","remote":"https://huis.voorbeeld.nl","home":"Thuis","server_id":"6F1C2E8A-1B2C-4D5E-8F90-123456789ABC","role":"viewer"}"""
        val r = PairingCodec.parse(text) as PairParse.Ok
        assertEquals("6f1c2e8a-1b2c-4d5e-8f90-123456789abc", r.serverId)
        assertEquals("viewer", r.role)
    }

    @Test
    fun rejectsHomeAddressOnTheInternet() {
        // The old fallback could put the tunnel host in "lan" as plain http: never accept that.
        val r = PairingCodec.parse("""{"v":1,"type":"shc-pair","code":"ABCD-1234","lan":"http://huis.voorbeeld.nl","remote":"https://huis.voorbeeld.nl"}""")
        assertTrue(r is PairParse.Invalid)
        assertTrue((r as PairParse.Invalid).reason.contains("thuisnetwerk"))
        // A remote address on the home network is refused as well.
        assertTrue(PairingCodec.parse("""{"v":1,"type":"shc-pair","code":"ABCD-1234","remote":"https://192.168.1.20"}""") is PairParse.Invalid)
        // Garbage server id.
        assertTrue(PairingCodec.parse("""{"v":1,"type":"shc-pair","code":"ABCD-1234","lan":"http://10.0.0.5:8088","server_id":"<script>"}""") is PairParse.Invalid)
    }

    @Test
    fun classifiesManualAddresses() {
        val lan = PairingCodec.manual("192.168.1.20:8088", "k7q4 9xpm") as PairParse.Ok
        assertEquals("http://192.168.1.20:8088", lan.lanUrl)
        assertNull(lan.remoteUrl)
        assertEquals("K7Q49XPM", lan.code)
        val lanHttps = PairingCodec.manual("https://nas.local", "ABCD-1234") as PairParse.Ok
        assertEquals("https://nas.local", lanHttps.lanUrl)
        val remote = PairingCodec.manual("https://huis.voorbeeld.nl", "ABCD-1234") as PairParse.Ok
        assertEquals("https://huis.voorbeeld.nl", remote.remoteUrl)
        assertNull(remote.lanUrl)
        assertTrue(PairingCodec.manual("huis.voorbeeld.nl", "ABCD-1234") is PairParse.Invalid) // http to the internet
        assertTrue(PairingCodec.manual("", "ABCD-1234") is PairParse.Invalid)
        assertTrue(PairingCodec.manual("192.168.1.20", "x") is PairParse.Invalid)
    }

    @Test
    fun refusalsAreCodesTheAppTranslates() {
        fun qr(json: String) = PairingCodec.parse(json) as PairParse.Invalid
        assertEquals(PairError.NOT_OURS, qr("https://example.com").error)
        assertEquals(PairError.NOT_OURS, qr("""{"v":1,"type":"wifi","code":"X"}""").error)
        assertEquals(PairError.NEWER_VERSION, qr("""{"v":2,"type":"shc-pair","code":"ABCD-1234","lan":"http://a"}""").error)
        assertEquals(PairError.CODE_MISSING, qr("""{"v":1,"type":"shc-pair","code":"x","lan":"http://10.0.0.5"}""").error)
        assertEquals(PairError.QR_NO_ADDRESS, qr("""{"v":1,"type":"shc-pair","code":"ABCD-1234"}""").error)
        assertEquals(PairError.QR_LAN_INVALID, qr("""{"v":1,"type":"shc-pair","code":"ABCD-1234","lan":"ftp://x"}""").error)
        assertEquals(PairError.QR_REMOTE_INVALID, qr("""{"v":1,"type":"shc-pair","code":"ABCD-1234","remote":"ftp://x"}""").error)
        val lanOnline = qr("""{"v":1,"type":"shc-pair","code":"ABCD-1234","lan":"http://huis.voorbeeld.nl"}""")
        assertEquals(PairError.ADDRESS_RULE, lanOnline.error)
        assertEquals(AddressProblem.LAN_NOT_HOME, lanOnline.address)
        assertEquals(AddressProblem.LAN_NOT_HOME.dutch, lanOnline.reason)
        assertEquals(AddressProblem.REMOTE_NOT_HTTPS, qr("""{"v":1,"type":"shc-pair","code":"ABCD-1234","remote":"http://huis.voorbeeld.nl"}""").address)
        assertEquals(PairError.NOT_OURS, qr("""{"v":1,"type":"shc-pair","code":"ABCD-1234","lan":"http://10.0.0.5:8088","server_id":"<script>"}""").error)

        fun manual(address: String, code: String) = PairingCodec.manual(address, code) as PairParse.Invalid
        assertEquals(PairError.ADDRESS_INVALID, manual("", "ABCD-1234").error)
        assertEquals(PairError.CODE_INVALID, manual("192.168.1.20", "x").error)
        assertEquals(PairError.NEEDS_HTTPS, manual("huis.voorbeeld.nl", "ABCD-1234").error)
        assertNull(manual("huis.voorbeeld.nl", "ABCD-1234").address)
        assertEquals(PairError.NEEDS_HTTPS.dutch, manual("huis.voorbeeld.nl", "ABCD-1234").reason)
    }
}
