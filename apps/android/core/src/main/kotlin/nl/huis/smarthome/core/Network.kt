package nl.huis.smarthome.core

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.io.IOException
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

// ---------------------------------------------------------------------------
// Which hosts count as "home network" (verbeterlijst 17).
//
// Android's network_security_config cannot express "cleartext only for private IP ranges"
// (it only knows domain names), so cleartext stays permitted there and this rule is enforced
// here, in code: a plain-http address is only used when its host is on the home network.
// Mirrors security.is_private_host on the server.
// ---------------------------------------------------------------------------

object HostRules {
    private val LOCAL_SUFFIXES = listOf(".local", ".lan", ".home.arpa", ".internal", ".localdomain")

    /** Private/link-local IP, or a `.local`-style or single-label name. Loopback is not. */
    fun isPrivateHost(host: String): Boolean {
        val h = host.trim().lowercase().removePrefix("[").removeSuffix("]").trimEnd('.')
        if (h.isEmpty()) return false
        if (h.contains(':')) return isPrivateV6(h)
        if (h.all { it.isDigit() || it == '.' }) {
            // Only a full dotted quad; "3232235777" or "192.168.1" would be parsed by the
            // resolver as some other (maybe public) address.
            val v4 = parseIpv4(h) ?: return false
            return isPrivateV4(v4)
        }
        if (h.startsWith("0x")) return false
        if (h == "localhost" || h.endsWith(".localhost")) return false
        return LOCAL_SUFFIXES.any { h.endsWith(it) } || !h.contains('.')
    }

    fun parseIpv4(h: String): IntArray? {
        val parts = h.split('.')
        if (parts.size != 4) return null
        val out = IntArray(4)
        for ((i, p) in parts.withIndex()) {
            if (p.isEmpty() || p.length > 3 || !p.all { it.isDigit() }) return null
            if (p.length > 1 && p.startsWith('0')) return null // octal-looking, ambiguous
            val n = p.toInt()
            if (n > 255) return null
            out[i] = n
        }
        return out
    }

    private fun isPrivateV4(o: IntArray): Boolean = when {
        o[0] == 10 -> true
        o[0] == 172 && o[1] in 16..31 -> true
        o[0] == 192 && o[1] == 168 -> true
        o[0] == 169 && o[1] == 254 -> true // link-local
        else -> false
    }

    private fun isPrivateV6(h: String): Boolean {
        // Contains ':' so this is an IPv6 literal: no DNS lookup happens.
        val addr = try {
            InetAddress.getByName(h)
        } catch (e: Exception) {
            return false
        }
        return when (addr) {
            is Inet4Address -> isPrivateV4(IntArray(4) { addr.address[it].toInt() and 0xff })
            is Inet6Address -> {
                val b0 = addr.address[0].toInt() and 0xff
                val b1 = addr.address[1].toInt() and 0xff
                (b0 and 0xfe) == 0xfc || (b0 == 0xfe && (b1 and 0xc0) == 0x80) // ULA fc00::/7, link-local fe80::/10
            }
            else -> false
        }
    }
}

/**
 * Why an address cannot be used. The app shows the matching text from its strings.xml (so
 * TalkBack and translations read the same resources); [dutch] is the same text for JVM callers
 * such as the QR-code parser.
 */
enum class AddressProblem(val dutch: String) {
    LAN_INVALID("Het thuisadres is geen geldig adres."),
    LAN_SCHEME("Het thuisadres moet met http:// of https:// beginnen."),
    LAN_NOT_HOME(
        "Het thuisadres moet een adres in je thuisnetwerk zijn (bijvoorbeeld http://192.168.1.20:8088 of http://nas.local:8088), geen internetadres.",
    ),
    REMOTE_INVALID("Het externe adres is geen geldig adres."),
    REMOTE_NOT_HTTPS("Het externe adres moet met https:// beginnen: je sleutel gaat dan over internet."),
    REMOTE_IS_HOME("Het externe adres is een adres in je thuisnetwerk; vul het adres van je tunnel in."),
}

/** Rules for the two server addresses the app uses (same semantics as the server's checks). */
object ServerUrls {
    /** Why [url] cannot be the home (LAN) address, or null when it is fine. */
    fun lanIssue(url: String): AddressProblem? {
        val u = url.toHttpUrlOrNull() ?: return AddressProblem.LAN_INVALID
        if (u.scheme != "http" && u.scheme != "https") return AddressProblem.LAN_SCHEME
        if (!HostRules.isPrivateHost(u.host)) return AddressProblem.LAN_NOT_HOME
        return null
    }

    /** Why [url] cannot be the address used outside the home, or null when it is fine. */
    fun remoteIssue(url: String): AddressProblem? {
        val u = url.toHttpUrlOrNull() ?: return AddressProblem.REMOTE_INVALID
        if (u.scheme != "https") return AddressProblem.REMOTE_NOT_HTTPS
        val h = u.host.lowercase()
        if (HostRules.isPrivateHost(h) || h == "localhost" || h.startsWith("127.") || h == "::1") {
            return AddressProblem.REMOTE_IS_HOME
        }
        return null
    }

    /** Dutch reason why [url] cannot be the home (LAN) address, or null when it is fine. */
    fun lanProblem(url: String): String? = lanIssue(url)?.dutch

    /** Dutch reason why [url] cannot be the address used outside the home, or null when it is fine. */
    fun remoteProblem(url: String): String? = remoteIssue(url)?.dutch

    /** Plain http is only ever used towards a home-network host. */
    fun cleartextAllowed(url: String): Boolean {
        val u = url.toHttpUrlOrNull() ?: return false
        return u.scheme == "https" || HostRules.isPrivateHost(u.host)
    }
}

// ---------------------------------------------------------------------------
// Server identity (verbeterlijst 17): GET /pairing/identity before any token is sent.
//
// The server id alone is public (it is in the QR code and anyone on the network can ask for it),
// so a paired phone also sends a fresh random nonce and its client id. Only the real server knows
// the hash of this phone's token and can answer with the right proof (IdentityProof). On the home
// address (often plain http) the token is only sent after a valid proof; over https to the tunnel
// a matching server id is enough when the server gives no proof, because TLS already proves the
// host.
// ---------------------------------------------------------------------------

enum class IdentityVerdict {
    /** Same server as at pairing (and, where required, it proved it): the token may be sent. */
    MATCH,

    /** Another server answered, or a wrong proof: never send the token ("Dit is niet jouw server"). */
    MISMATCH,

    /** Something answered, but not with an identity of this app (old server, router, wifi login page). */
    NOT_A_SERVER,

    /** Pairing from before server ids existed: nothing to compare with. */
    UNKNOWN_EXPECTED,

    /** The home address names our server id but did not answer the challenge: not trusted. */
    UNPROVEN,

    /** Pairing from before client ids were stored: no challenge possible on the home address. */
    NO_CHALLENGE,
}

/** The outcome of the challenge in one identity answer. */
enum class ProofState {
    /** No challenge was sent (no token or no client id on this phone yet). */
    NOT_ASKED,

    /** Challenge sent, but the answer has no (usable) proof: old server, unknown client, or an impostor. */
    MISSING,

    /** The proof matches this phone's token: the real server. */
    VALID,

    /** A proof that does not match: not our server (or a token the server no longer has). */
    INVALID,
}

object ServerIdentityCheck {
    private val ID = Regex("^[0-9a-f-]{8,64}$")

    fun normalize(id: String?): String? = id?.trim()?.lowercase()?.takeIf { ID.matches(it) }

    /** Only compares server ids: used before a token exists (pairing). */
    fun verify(expected: String?, actual: String?): IdentityVerdict {
        val a = normalize(actual) ?: return IdentityVerdict.NOT_A_SERVER
        val e = normalize(expected) ?: return IdentityVerdict.UNKNOWN_EXPECTED
        return if (a == e) IdentityVerdict.MATCH else IdentityVerdict.MISMATCH
    }

    /**
     * Whether the token may go to [kind] after this identity answer. The home address needs a
     * valid proof; the tunnel (https only) accepts a matching id when the server sent no proof,
     * but never a wrong proof.
     */
    fun verifyRoute(expected: String?, actual: String?, proof: ProofState, kind: RouteKind): IdentityVerdict {
        val ids = verify(expected, actual)
        if (ids != IdentityVerdict.MATCH) return ids
        return when (proof) {
            ProofState.VALID -> IdentityVerdict.MATCH
            ProofState.INVALID -> IdentityVerdict.MISMATCH
            ProofState.MISSING -> if (kind == RouteKind.REMOTE) IdentityVerdict.MATCH else IdentityVerdict.UNPROVEN
            ProofState.NOT_ASKED -> if (kind == RouteKind.REMOTE) IdentityVerdict.MATCH else IdentityVerdict.NO_CHALLENGE
        }
    }
}

/** One identity request's challenge: a fresh random nonce and this phone's client id. */
class IdentityChallenge private constructor(val nonce: String, val clientId: String) {
    /** Query string for GET /pairing/identity (both values are URL-safe by construction). */
    val query: String get() = "nonce=$nonce&client_id=$clientId"

    companion object {
        private val CLIENT_ID = Regex("^[0-9a-fA-F-]{8,64}$")
        private val NONCE = Regex("^[A-Za-z0-9_-]{16,128}$")
        private val random = SecureRandom()

        /** A new challenge, or null when this phone has no (valid) client id to ask with. */
        fun create(clientId: String?): IdentityChallenge? {
            val id = clientId?.trim()?.lowercase()?.takeIf { CLIENT_ID.matches(it) } ?: return null
            return IdentityChallenge(newNonce(), id)
        }

        /** For tests: a challenge with a fixed nonce. */
        fun of(nonce: String, clientId: String): IdentityChallenge {
            require(NONCE.matches(nonce)) { "nonce" }
            require(CLIENT_ID.matches(clientId)) { "client id" }
            return IdentityChallenge(nonce, clientId.lowercase())
        }

        /** 32 random bytes, base64url without padding (43 characters). */
        fun newNonce(): String {
            val bytes = ByteArray(32)
            random.nextBytes(bytes)
            return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
        }
    }
}

/**
 * The server's answer to an [IdentityChallenge] (contract with `identity_proof` in
 * apps/api/src/shc_api/routers/pairing.py, algorithm "hmac-sha256-v1"):
 * HMAC-SHA256 with as key the UTF-8 bytes of sha256(token) in lowercase hex (what the server
 * stores), over the UTF-8 lines "shc-identity-v1", server_id and nonce joined by "\n"; lowercase hex.
 */
object IdentityProof {
    const val ALG = "hmac-sha256-v1"

    fun expected(token: String, serverId: String, nonce: String): String {
        val key = hex(MessageDigest.getInstance("SHA-256").digest(token.toByteArray(Charsets.UTF_8)))
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return hex(mac.doFinal("shc-identity-v1\n$serverId\n$nonce".toByteArray(Charsets.UTF_8)))
    }

    /** Checks [answer] against the challenge that was sent with it. */
    fun check(challenge: IdentityChallenge?, token: String?, answer: ServerIdentity): ProofState {
        if (challenge == null || token.isNullOrEmpty()) return ProofState.NOT_ASKED
        val proof = answer.proof?.trim()?.lowercase()?.takeIf { it.isNotEmpty() } ?: return ProofState.MISSING
        if (answer.proofAlg != null && answer.proofAlg != ALG) return ProofState.MISSING
        val serverId = answer.serverId ?: return ProofState.MISSING
        val want = expected(token, serverId, challenge.nonce)
        // Constant time: a wrong proof must not reveal how much of it was right.
        return if (MessageDigest.isEqual(want.toByteArray(Charsets.UTF_8), proof.toByteArray(Charsets.UTF_8))) {
            ProofState.VALID
        } else {
            ProofState.INVALID
        }
    }

    private fun hex(bytes: ByteArray): String = buildString(bytes.size * 2) {
        bytes.forEach { b -> append(Character.forDigit((b.toInt() shr 4) and 0xf, 16)).append(Character.forDigit(b.toInt() and 0xf, 16)) }
    }
}

// ---------------------------------------------------------------------------
// Connection route: LAN first, then the Cloudflare tunnel, both identity-checked.
// ---------------------------------------------------------------------------

enum class RouteKind { LAN, REMOTE }

/**
 * A usable route. [serverId] is set only after the identity check passed on this address:
 * the API client refuses to attach the token to a route without it.
 */
data class Route(val kind: RouteKind, val baseUrl: String, val serverId: String? = null) {
    val verified: Boolean get() = serverId != null
}

/** Result of asking an address who it is. */
sealed interface Probe {
    /**
     * The address answered over HTTP; [serverId] is null when the answer was not an identity.
     * [proof] says whether it answered this phone's challenge.
     */
    data class Answered(val serverId: String?, val proof: ProofState = ProofState.NOT_ASKED) : Probe

    /** No answer: network error, timeout, 5xx or a Cloudflare error page. */
    data object Unreachable : Probe

    /**
     * Something in between refused us (Cloudflare Access without a valid service token). The app
     * shows the strings.xml text for [problem]; [reason] is the Dutch text for JVM callers.
     */
    data class Blocked(val problem: ApiProblem) : Probe {
        val reason: String get() = problem.dutch
    }

    companion object {
        /** Maps a failed identity request to a probe result. */
        fun fromFailure(e: Throwable): Probe = when {
            e is ApiException && e.code == "cf_access_login" -> Blocked(e.problem ?: ApiProblem.CF_ACCESS_LOGIN)
            e is ApiException && e.status == 403 && !e.fromApi -> Blocked(e.problem ?: ApiProblem.CF_FORBIDDEN)
            e is ApiException && e.routeProblem && e.status >= 500 -> Unreachable
            e is ApiException && e.code == "api_unavailable" -> Unreachable
            e is ApiException -> Answered(null) // e.g. 404 from an old server, a redirect or an HTML page
            else -> Unreachable
        }
    }
}

data class RouteChoice(
    val route: Route?,
    /** An address answered but it was not this phone's server. */
    val wrongServer: Boolean = false,
    /** Paired before server ids existed and only the home address could be tried. */
    val unverifiable: Boolean = false,
    /** Server id learned on a legacy pairing (over https only); the caller stores it. */
    val learnedServerId: String? = null,
    /** Why the tunnel refused us (Cloudflare Access); the app shows its strings.xml text. */
    val blocked: ApiProblem? = null,
) {
    /** Dutch text of [blocked], for JVM callers. */
    val blockedReason: String? get() = blocked?.dutch
}

object RouteSelector {
    /**
     * Probes the LAN address first (the caller uses a short timeout for it) and falls back to the
     * remote address. An address is only used when it obeys [ServerUrls] and its identity matches
     * [expectedServerId]: on the home address that includes a valid answer to the challenge
     * ([ServerIdentityCheck.verifyRoute]). A legacy pairing (no expected id) may learn the id over
     * https only; one without a client id can only use the tunnel until it has learned it.
     */
    suspend fun select(
        lanUrl: String?,
        remoteUrl: String?,
        expectedServerId: String?,
        probe: suspend (baseUrl: String, kind: RouteKind) -> Probe,
    ): RouteChoice {
        val candidates = buildList {
            if (lanUrl != null && ServerUrls.lanProblem(lanUrl) == null) add(lanUrl to RouteKind.LAN)
            if (remoteUrl != null && ServerUrls.remoteProblem(remoteUrl) == null) add(remoteUrl to RouteKind.REMOTE)
        }
        val hasRemote = candidates.any { it.second == RouteKind.REMOTE }
        var wrong = false
        var unverifiable = false
        var blocked: ApiProblem? = null
        for ((base, kind) in candidates) {
            when (val p = probe(base, kind)) {
                Probe.Unreachable -> Unit
                is Probe.Blocked -> blocked = p.problem
                is Probe.Answered -> when (ServerIdentityCheck.verifyRoute(expectedServerId, p.serverId, p.proof, kind)) {
                    IdentityVerdict.MATCH ->
                        return RouteChoice(Route(kind, base, ServerIdentityCheck.normalize(p.serverId)))
                    IdentityVerdict.UNKNOWN_EXPECTED -> {
                        if (kind == RouteKind.REMOTE) {
                            val id = ServerIdentityCheck.normalize(p.serverId)
                            return RouteChoice(Route(kind, base, id), learnedServerId = id)
                        }
                        unverifiable = true
                    }
                    // Without a tunnel this phone can never learn its client id: pair again.
                    // With one, wait for the tunnel instead of forcing that.
                    IdentityVerdict.NO_CHALLENGE -> if (!hasRemote) unverifiable = true
                    IdentityVerdict.MISMATCH, IdentityVerdict.NOT_A_SERVER, IdentityVerdict.UNPROVEN -> wrong = true
                }
            }
        }
        return RouteChoice(null, wrongServer = wrong, unverifiable = unverifiable && !wrong, blocked = blocked)
    }
}

/** Where calls get their route from; [RouteManager] in the app, a fake in tests. */
interface RouteSource {
    /** The current route, or a freshly picked one (LAN first, then remote). */
    suspend fun choose(): RouteChoice

    /** Forget [route] (or the current route when null) so the next call picks again. */
    fun forget(route: Route? = null): Boolean
}

class NoRouteException(val choice: RouteChoice?) : IOException(
    when {
        choice?.wrongServer == true -> "Dit is niet jouw server. De app stuurt je sleutel er niet heen."
        else -> choice?.blocked?.dutch ?: "Geen verbinding met je huis."
    },
)

/**
 * Remembers the chosen route in memory only (so it is forgotten on every app start) and picks a
 * new one when asked after [forget] — the app calls [forget] on every network change.
 */
class RouteManager(
    private val target: () -> RouteTarget?,
    /** Called after every fresh selection (store a learned server id, show "wrong server"). */
    private val onChoice: (RouteChoice) -> Unit = {},
    private val probe: suspend (baseUrl: String, kind: RouteKind) -> Probe,
) : RouteSource {
    data class RouteTarget(val lanUrl: String?, val remoteUrl: String?, val serverId: String?)

    private val mutex = Mutex()

    @Volatile private var current: Route? = null

    @Volatile private var generation = 0L

    private val _choice = MutableStateFlow<RouteChoice?>(null)

    /** The last selection result (null before the first one); drives "wrong server" in the UI. */
    val lastChoice: StateFlow<RouteChoice?> = _choice.asStateFlow()

    val route: Route? get() = current

    override suspend fun choose(): RouteChoice {
        current?.let { return RouteChoice(it) }
        return mutex.withLock {
            current?.let { return@withLock RouteChoice(it) }
            val gen = generation
            val t = target() ?: return@withLock RouteChoice(null)
            val choice = RouteSelector.select(t.lanUrl, t.remoteUrl, t.serverId, probe)
            // A network change during the probe makes the answer unreliable: use it once, don't keep it.
            if (gen == generation) current = choice.route
            _choice.value = choice
            onChoice(choice)
            choice
        }
    }

    /** Use a route that was just verified elsewhere (right after pairing). */
    fun adopt(route: Route) {
        require(route.verified) { "only verified routes" }
        generation++
        current = route
        _choice.value = RouteChoice(route)
    }

    override fun forget(route: Route?): Boolean {
        val c = current
        if (route != null && c != route) return false
        current = null
        generation++
        return c != null
    }
}

// ---------------------------------------------------------------------------
// Failure classification (verbeterlijst 37) and the single retry helper.
// ---------------------------------------------------------------------------

enum class FailureKind {
    /** No network answer at all. */
    NETWORK,

    /** 5xx, Cloudflare 52x/530, an HTML page instead of the API, or a redirect: treat as offline. */
    SERVER_DOWN,

    /** nginx says the API is starting or being updated (503 api_unavailable). */
    STARTING,

    /** No address answered as this phone's server. */
    NO_ROUTE,

    /** An address answered, but it was another server: the token was not sent. */
    WRONG_SERVER,

    /** The server says this phone's token is revoked, expired or unknown. */
    UNPAIRED,

    /** 403 from the API: this phone (or user) may not do this. */
    FORBIDDEN,

    /** Any other answer from the API with a Dutch message (409 policy, 422 validation, …). */
    APP_ERROR;

    /** Worth forgetting the route and picking again once. */
    val repick: Boolean get() = this == NETWORK || this == SERVER_DOWN || this == STARTING

    /** Shown as the offline banner, not as an error message. */
    val offline: Boolean get() = this == NETWORK || this == SERVER_DOWN || this == STARTING || this == NO_ROUTE || this == WRONG_SERVER
}

object Failures {
    fun classify(e: Throwable): FailureKind = when (e) {
        is NoRouteException -> if (e.choice?.wrongServer == true) FailureKind.WRONG_SERVER else FailureKind.NO_ROUTE
        is ApiException -> when {
            e.unpairReason != null -> FailureKind.UNPAIRED
            e.code == "api_unavailable" -> FailureKind.STARTING
            e.routeProblem -> FailureKind.SERVER_DOWN
            e.status == 403 -> FailureKind.FORBIDDEN
            else -> FailureKind.APP_ERROR
        }
        is IOException -> FailureKind.NETWORK
        else -> FailureKind.APP_ERROR
    }
}

object Routed {
    /**
     * Runs [block] on the current route. On a network error or a server/Cloudflare error it forgets
     * that route, picks again once (LAN first, then remote) and retries once. Used by the app,
     * the background worker and the widget refresh alike.
     */
    suspend fun <T> call(source: RouteSource, block: suspend (Route) -> T): T {
        val first = source.choose()
        val route = first.route ?: throw NoRouteException(first)
        try {
            return block(route)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (!Failures.classify(e).repick) throw e
            source.forget(route)
            val second = source.choose().route ?: throw e
            try {
                return block(second)
            } catch (e2: CancellationException) {
                throw e2
            } catch (e2: Exception) {
                if (Failures.classify(e2).repick) source.forget(second)
                throw e2
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Connection status shown in the UI, and messages only when it changes.
// ---------------------------------------------------------------------------

enum class ConnectionStatus {
    /** Not tried yet (app just opened). */
    CONNECTING,
    ONLINE,
    OFFLINE,

    /** The server is starting or being updated. */
    STARTING,

    /** The home address answered as another server; the token was not sent. */
    WRONG_SERVER,

    /** Revoked, expired or unknown token: the phone must be paired again. */
    UNPAIRED;

    companion object {
        fun from(kind: FailureKind): ConnectionStatus? = when (kind) {
            FailureKind.NETWORK, FailureKind.SERVER_DOWN, FailureKind.NO_ROUTE -> OFFLINE
            FailureKind.STARTING -> STARTING
            FailureKind.WRONG_SERVER -> WRONG_SERVER
            FailureKind.UNPAIRED -> UNPAIRED
            FailureKind.FORBIDDEN, FailureKind.APP_ERROR -> null // the connection itself is fine
        }
    }
}

object ConnectionText {
    /** Dutch explanation for the banner; null when online. */
    fun banner(status: ConnectionStatus): String? = when (status) {
        ConnectionStatus.ONLINE -> null
        ConnectionStatus.CONNECTING -> "Verbinden met je huis…"
        ConnectionStatus.OFFLINE -> "Geen verbinding met je huis."
        ConnectionStatus.STARTING -> "Je huis start op of wordt bijgewerkt. Even geduld."
        ConnectionStatus.WRONG_SERVER -> "Dit is niet jouw server. Het thuisadres antwoordt, maar het is een ander apparaat. De app stuurt je sleutel er niet heen."
        ConnectionStatus.UNPAIRED -> "Deze telefoon is niet meer gekoppeld."
    }

    /**
     * A one-off message when the status changes (never on every failed poll). Null when the
     * banner already says enough. The app shows its strings.xml text for [ConnectionText.change].
     */
    fun transition(old: ConnectionStatus, new: ConnectionStatus): String? = change(old, new)?.dutch

    /** Which one-off connection message fits this status change, if any. */
    fun change(old: ConnectionStatus, new: ConnectionStatus): ConnectionChange? = when {
        old == new -> null
        old == ConnectionStatus.ONLINE && new == ConnectionStatus.OFFLINE -> ConnectionChange.LOST
        old == ConnectionStatus.ONLINE && new == ConnectionStatus.STARTING -> ConnectionChange.RESTARTING
        new == ConnectionStatus.WRONG_SERVER -> ConnectionChange.WRONG_SERVER
        new == ConnectionStatus.ONLINE && (old == ConnectionStatus.OFFLINE || old == ConnectionStatus.STARTING) -> ConnectionChange.RESTORED
        else -> null
    }
}

/** A connection change worth a one-off message; [dutch] mirrors the app's strings.xml text. */
enum class ConnectionChange(val dutch: String) {
    LOST("Verbinding met je huis verbroken."),
    RESTARTING("Je huis wordt bijgewerkt of start opnieuw op."),
    WRONG_SERVER("Dit is niet jouw server. De app stuurt je sleutel er niet heen."),
    RESTORED("Weer verbonden met je huis."),
}

// ---------------------------------------------------------------------------
// Addresses from GET /system/settings (verbeterlijst 37).
// ---------------------------------------------------------------------------

data class Addresses(val lanUrl: String?, val remoteUrl: String?)

object AddressSync {
    /**
     * New addresses when the server reports a valid, different one; null when nothing changes.
     * An empty or invalid value from the server never clears or replaces a working address.
     * Every address is identity-checked before the token goes there, so this cannot redirect it.
     */
    fun merge(current: Addresses, lan: String?, remote: String?): Addresses? {
        val newLan = lan?.let { UrlTools.normalizeBaseUrl(it) }?.takeIf { ServerUrls.lanProblem(it) == null }
        val newRemote = remote?.let { UrlTools.normalizeBaseUrl(it) }?.takeIf { ServerUrls.remoteProblem(it) == null }
        val merged = Addresses(newLan ?: current.lanUrl, newRemote ?: current.remoteUrl)
        return merged.takeIf { it != current }
    }
}

/**
 * Turns default-network callbacks into "the network changed" (verbeterlijst 37): the first report
 * after registering is the current network and is not a change; losing the network or switching
 * to another one is. Capability updates on the same network (signal strength) are not.
 */
class NetworkChangeDetector {
    private var seen = false
    private var last: Long? = null

    /** A network became the default one ([handle] = Network.getNetworkHandle()). True on a change. */
    @Synchronized
    fun onAvailable(handle: Long): Boolean {
        val changed = seen && handle != last
        seen = true
        last = handle
        return changed
    }

    /** A network was lost; only a change when it was the default one. */
    @Synchronized
    fun onLost(handle: Long): Boolean {
        if (!seen || handle != last) return false
        last = null
        return true
    }
}
